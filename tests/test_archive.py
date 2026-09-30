import json
import sqlite3

from wechat_cli.core.archive import import_moments_json


def _write_payload(path, moments):
    path.write_text(json.dumps({"moments": moments}, ensure_ascii=False), encoding="utf-8")


def test_archive_import_is_idempotent_and_keeps_versions(tmp_path):
    source = tmp_path / "moments.json"
    record = {
        "id": "m-1",
        "time": "2026-09-29T12:30:00+08:00",
        "text": "第一条",
        "images": [],
        "capture_source": "local_sns_cache",
        "ownership_verification": "xml_author",
    }
    _write_payload(source, [record])
    archive = tmp_path / "archive"
    first = import_moments_json(source, archive, batch_id="batch-one")
    second = import_moments_json(source, archive, batch_id="batch-two")
    assert first["new"] == 1
    assert second["duplicate"] == 1
    with sqlite3.connect(archive / "moments.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM moments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM moment_versions").fetchone()[0] == 1


def test_archive_updates_current_record_and_preserves_old_version(tmp_path):
    source = tmp_path / "moments.json"
    record = {
        "id": "m-edit",
        "time": "2026-09-29T12:30:00+08:00",
        "text": "初稿",
        "images": [],
        "capture_source": "local_sns_cache",
        "ownership_verification": "xml_author",
    }
    _write_payload(source, [record])
    archive = tmp_path / "archive"
    import_moments_json(source, archive, batch_id="edit-one")
    _write_payload(source, [{**record, "text": "修订稿"}])
    stats = import_moments_json(source, archive, batch_id="edit-two")
    assert stats["updated"] == 1
    with sqlite3.connect(archive / "moments.sqlite") as conn:
        assert conn.execute("SELECT text, version FROM moments").fetchone() == ("修订稿", 2)
        assert conn.execute("SELECT COUNT(*) FROM moment_versions").fetchone()[0] == 2


def test_archive_keeps_same_text_posts_with_distinct_ids(tmp_path):
    source = tmp_path / "moments.json"
    base = {
        "time": "2026-09-29T12:30:00+08:00",
        "text": "相同文字",
        "images": [],
        "capture_source": "local_sns_cache",
        "ownership_verification": "xml_author",
    }
    _write_payload(source, [{**base, "id": "m-1"}, {**base, "id": "m-2"}])
    stats = import_moments_json(source, tmp_path / "archive", batch_id="same-text")
    assert stats["new"] == 2


def test_archive_copies_media_to_relative_asset_path(tmp_path):
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"image-data")
    source = tmp_path / "moments.json"
    _write_payload(source, [{
        "id": "m-asset",
        "time": "2026-09-29T12:30:00+08:00",
        "text": "带图",
        "images": [{"local_path": str(media)}],
        "capture_source": "local_sns_cache",
        "ownership_verification": "xml_author",
    }])
    archive = tmp_path / "archive"
    import_moments_json(source, archive, batch_id="assets")
    with sqlite3.connect(archive / "moments.sqlite") as conn:
        record_json = conn.execute("SELECT record_json FROM moments").fetchone()[0]
        record = json.loads(record_json)
        assert record["images"][0]["local_path"].startswith("assets/")
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert (archive / record["images"][0]["local_path"]).is_file()


def test_archive_marks_ui_without_full_year_as_incomplete(tmp_path):
    source = tmp_path / "moments.json"
    _write_payload(source, [{
        "id": "ui-1",
        "time": "3月4日",
        "text": "页面记录",
        "images": [],
        "capture_source": "wechat_ui",
        "ownership_verification": "user_confirmed_ui",
    }])
    stats = import_moments_json(source, tmp_path / "archive", batch_id="ui-date")
    assert stats["incomplete"] == 1
    with sqlite3.connect(tmp_path / "archive" / "moments.sqlite") as conn:
        assert conn.execute("SELECT status FROM moments").fetchone()[0] == "incomplete"


def test_archive_parses_full_year_chinese_ui_time_but_marks_timezone_uncertain(tmp_path):
    source = tmp_path / "moments.json"
    _write_payload(source, [{
        "id": "ui-cn",
        "time": "2026年9月29日 12:30",
        "text": "中文日期",
        "images": [],
        "capture_source": "wechat_ui",
        "ownership_verification": "user_confirmed_ui",
    }])
    archive = tmp_path / "archive"
    import_moments_json(source, archive, batch_id="ui-cn")
    with sqlite3.connect(archive / "moments.sqlite") as conn:
        assert conn.execute("SELECT time_precision, status FROM moments").fetchone() == ("minute", "incomplete")
