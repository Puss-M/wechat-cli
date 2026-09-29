import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from wechat_cli.commands.moments import moments
from wechat_cli.core.moments import (
    MomentDataError,
    MomentImageDownloadError,
    _HttpOnlyRedirectHandler,
    download_own_moment_images,
    load_own_moments,
)


def _xml(moment_id, username, timestamp, text):
    from xml.sax.saxutils import escape

    return (
        "<SnsDataItem><TimelineObject>"
        f"<id>{moment_id}</id><username>{username}</username>"
        f"<createTime>{timestamp}</createTime><contentDesc>{escape(text)}</contentDesc>"
        "</TimelineObject></SnsDataItem>"
    )


def _database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE SnsTimeLine (tid INTEGER, user_name TEXT, content TEXT)")
        conn.executemany(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            [
                (1, "self", _xml("101", "self", 1700000000, "第一条 <文字>")),
                (2, "other", _xml("102", "other", 1700000100, "不应导出")),
                (3, "self", _xml("103", "self", 1700000200, "")),
                (4, "self", _xml("104", "self", 1700000300, "最新一条")),
            ],
        )


def test_load_filters_by_account_and_keeps_order(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    records, skipped = load_own_moments(db_path, "self")
    assert [record["id"] for record in records] == ["104", "101"]
    assert records[1]["text"] == "第一条 <文字>"
    assert records[0]["time"].startswith("2023-")
    assert skipped == 1

    all_records, skipped = load_own_moments(db_path, "self", include_empty=True)
    assert [record["id"] for record in all_records] == ["104", "103", "101"]
    assert skipped == 0


def test_sort_uses_epoch_across_daylight_saving_offset(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            (5, "self", _xml("105", "self", 1730615400, "later absolute time")),
        )

    class LocalDateTime:
        @staticmethod
        def fromtimestamp(value):
            class ZonedTime:
                def astimezone(self):
                    return self

                def isoformat(self, timespec):
                    return {
                        1700000000: "2024-11-03T01:45:00-04:00",
                        1730615400: "2024-11-03T01:30:00-05:00",
                    }.get(value, "2023-01-01T00:00:00+00:00")

            return ZonedTime()

    with patch("wechat_cli.core.moments.datetime", LocalDateTime):
        records, _ = load_own_moments(db_path, "self")
    assert records[0]["id"] == "105"


def test_exports_images_location_and_link_metadata(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    rich_xml = (
        "<SnsDataItem><TimelineObject>"
        "<id>105</id><username>self</username><createTime>1700000400</createTime>"
        "<contentDesc></contentDesc>"
        "<location city='上海' poiName='外滩' poiAddress='中山东一路' latitude='31.24' longitude='121.49'/>"
        "<ContentObject><title>分享标题</title><description>链接摘要</description>"
        "<contentUrl>https://example.com/article</contentUrl><mediaList>"
        "<media><id>img-1</id><type>2</type><url>https://mmsns.qpic.cn/photo.jpg</url>"
        "<thumb>https://mmsns.qpic.cn/thumb.jpg</thumb><size>1234</size></media>"
        "<media><id>img-2</id><type>2</type><url>https://cdn.example.org/photo.png</url></media>"
        "<media><type>3</type><url>https://fakeqpic.cn/song</url></media>"
        "</mediaList></ContentObject>"
        "</TimelineObject></SnsDataItem>"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?)", (5, "self", rich_xml))

    records, skipped = load_own_moments(db_path, "self")
    record = next(item for item in records if item["id"] == "105")
    assert skipped == 1
    assert record["images"] == [
        {
            "id": "img-1",
            "url": "https://mmsns.qpic.cn/photo.jpg",
            "thumbnail_url": "https://mmsns.qpic.cn/thumb.jpg",
        },
        {"id": "img-2", "url": "https://cdn.example.org/photo.png", "thumbnail_url": None},
    ]
    assert record["media"][2]["kind"] == "other"
    assert record["location"] == {
        "city": "上海",
        "poiName": "外滩",
        "poiAddress": "中山东一路",
        "latitude": "31.24",
        "longitude": "121.49",
    }
    assert record["link"] == {
        "url": "https://example.com/article",
        "title": "分享标题",
        "description": "链接摘要",
    }


def test_bad_rows_are_skipped_without_losing_good_rows(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            (5, "self", _xml("105", "other", 1700000400, "错误归属")),
        )
        conn.execute(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            (6, "legacy_alias", _xml("106", "self", 1700000500, "数据库行作者是旧别名")),
        )
        conn.execute(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            (7, "self", "<broken"),
        )
    records, skipped, diagnostics = load_own_moments(
        db_path, "self", return_diagnostics=True
    )
    assert [record["id"] for record in records] == ["106", "104", "101"]
    assert skipped == 1
    assert diagnostics["candidate_count"] == 7
    assert diagnostics["matched_author_count"] == 4
    assert diagnostics["skipped_invalid"] == 3
    assert {item["tid"] for item in diagnostics["skipped_records"]} == {2, 5, 7}


def test_strict_mode_still_aborts_on_bad_row(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO SnsTimeLine VALUES (?, ?, ?)",
            (5, "self", _xml("105", "other", 1700000400, "错误归属")),
        )
    with pytest.raises(MomentDataError, match="作者"):
        load_own_moments(db_path, "self", strict=True)


def test_missing_xml_author_is_never_exported(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    xml = (
        "<SnsDataItem><TimelineObject><id>108</id>"
        "<createTime>1700000600</createTime><contentDesc>没有作者</contentDesc>"
        "</TimelineObject></SnsDataItem>"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?)", (8, "self", xml))
    records, _, diagnostics = load_own_moments(
        db_path, "self", return_diagnostics=True
    )
    assert "108" not in {record["id"] for record in records}
    assert any(item["tid"] == 8 and "作者" in item["reason"] for item in diagnostics["skipped_records"])


def test_nested_username_is_not_treated_as_post_author(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    xml = (
        "<SnsDataItem><TimelineObject><id>109</id><createTime>1700000700</createTime>"
        "<contentDesc>他人的分享</contentDesc><ContentObject>"
        "<username>self</username><title>分享内容</title></ContentObject>"
        "</TimelineObject></SnsDataItem>"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?)", (9, "other", xml))
    records, _, diagnostics = load_own_moments(
        db_path, "self", return_diagnostics=True
    )
    assert "109" not in {record["id"] for record in records}
    assert any(item["tid"] == 9 for item in diagnostics["skipped_records"])


def test_namespaced_nested_xml_and_millisecond_timestamp_are_supported(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    xml = (
        '<wx:SnsDataItem xmlns:wx="urn:wx"><wx:Meta><wx:TimelineObject>'
        '<wx:id>107</wx:id><wx:username>self</wx:username>'
        '<wx:createTime>1700000600000</wx:createTime>'
        '<wx:contentDesc>命名空间记录</wx:contentDesc>'
        "</wx:TimelineObject></wx:Meta></wx:SnsDataItem>"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?)", (8, "alias", xml))
    records, _ = load_own_moments(db_path, "self")
    record = next(item for item in records if item["id"] == "107")
    assert record["text"] == "命名空间记录"
    assert record["time"].startswith("2023-")


def test_richer_duplicate_replaces_empty_duplicate(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    richer = _xml("103", "self", 1700000200, "后来补齐的文字")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO SnsTimeLine VALUES (?, ?, ?)", (8, "self", richer))
    records, skipped = load_own_moments(db_path, "self")
    assert next(item for item in records if item["id"] == "103")["text"] == "后来补齐的文字"
    assert skipped == 1


def test_downloads_only_images_attached_to_own_records(tmp_path):
    moments = [{
        "id": "own-105",
        "images": [{"url": "https://example.test/own.jpg", "thumbnail_url": None}],
    }]
    with patch(
        "wechat_cli.core.moments._fetch_image",
        return_value=(b"\xff\xd8\xffown\xff\xd9", "image/jpeg"),
    ):
        stats = download_own_moment_images(moments, tmp_path / "images")
    target = tmp_path / "images" / "moments" / "own-105" / "01.jpg"
    assert target.read_bytes() == b"\xff\xd8\xffown\xff\xd9"
    assert moments[0]["images"][0]["local_path"] == str(target.resolve())
    assert stats == {"downloaded": 1, "reused": 0, "failed": 0}


def test_download_redirect_rejects_non_http_schemes():
    handler = _HttpOnlyRedirectHandler()
    with pytest.raises(MomentImageDownloadError, match="不支持的协议"):
        handler.redirect_request(None, None, 302, "Found", {}, "file:///private/data")


def test_command_writes_json_without_overwriting(tmp_path):
    db_path = tmp_path / "sns.db"
    _database(db_path)
    output = tmp_path / "mine.json"

    class Cache:
        def get(self, key):
            assert Path(key) == Path("sns/sns.db")
            return db_path

    class App:
        db_dir = "unused"
        decrypted_dir = "unused"
        cache = Cache()

    with patch("wechat_cli.commands.moments.get_self_username", return_value="self"):
        result = CliRunner().invoke(moments, ["--output", str(output)], obj=App())
        assert result.exit_code == 0, result.output
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["count"] == 2
        assert payload["skipped_empty"] == 1
        assert all(record["text"] != "不应导出" for record in payload["moments"])

        second = CliRunner().invoke(moments, ["--output", str(output)], obj=App())
        assert second.exit_code != 0
        assert "未覆盖" in second.output
