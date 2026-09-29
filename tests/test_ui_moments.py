import sys
import types

from wechat_cli.core.ui_moments import collect_visible_moments
from wechat_cli.core.ui_moments import merge_moment_records, parse_visible_moments


def test_parse_visible_moments_keeps_text_urls_and_screenshot():
    records = parse_visible_moments(
        ["2026年9月29日 12:30", "今天的记录", "链接 https://example.com/a"],
        "C:/captures/page-0001.png",
        1,
    )
    assert len(records) == 1
    record = records[0].as_record()
    assert record["text"] == "2026年9月29日 12:30\n今天的记录\n链接"
    assert record["link"]["url"] == "https://example.com/a"
    assert record["capture_screenshot"].endswith("page-0001.png")


def test_merge_keeps_richer_local_record_and_adds_ui_evidence():
    local = [{
        "id": "xml-1",
        "time": "2026-09-29T12:30:00+08:00",
        "text": "同一条",
        "images": [],
        "media": [],
        "location": {"city": "上海"},
        "link": None,
    }]
    ui = [{
        "id": "ui-1",
        "time": "2026-09-29T12:30:00+08:00",
        "text": "同一条",
        "capture_page": 2,
        "capture_screenshot": "page.png",
    }]
    merged = merge_moment_records(local, ui)
    assert len(merged) == 1
    assert merged[0]["location"] == {"city": "上海"}
    assert merged[0]["ui_captures"] == [{"page": 2, "screenshot": "page.png"}]


def test_merge_preserves_distinct_ui_records():
    merged = merge_moment_records([], [{"id": "ui-1", "time": None, "text": "一条"}])
    assert len(merged) == 1


def test_merge_normalizes_date_formats():
    local = [{"id": "xml", "time": "2026-09-29T12:30:00+08:00", "text": "同一条"}]
    ui = [{"id": "ui", "time": "2026年9月29日 12:30", "text": "同一条",
           "capture_page": 1, "capture_screenshot": "page.png"}]
    merged = merge_moment_records(local, ui)
    assert len(merged) == 1


def test_collect_visible_moments_writes_incremental_manifest(tmp_path, monkeypatch):
    class Control:
        def __init__(self, text):
            self.text = text

        def is_visible(self):
            return True

        def window_text(self):
            return self.text

    class Window:
        def windows(self, visible_only=True):
            return [self]

        def window_text(self):
            return "微信"

        def descendants(self):
            return [Control("2026年9月29日"), Control("一条记录")]

        def restore(self):
            return None

        def set_focus(self):
            return None

        def rectangle(self):
            return types.SimpleNamespace(left=0, top=0, right=20, bottom=20)

    class Desktop:
        def __init__(self, backend):
            assert backend == "uia"

        def windows(self, visible_only=True):
            return [Window()]

    pywinauto = types.SimpleNamespace(Desktop=Desktop)
    class ImageStub:
        width = 20
        height = 20

        def crop(self, box):
            assert box == (0, 0, 20, 20)
            return self

        def save(self, path):
            path.write_bytes(b"PNG")

    pyautogui = types.SimpleNamespace(
        screenshot=lambda: ImageStub(),
        moveTo=lambda *args, **kwargs: None,
        scroll=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "pywinauto", pywinauto)
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)

    records, stats = collect_visible_moments(tmp_path, max_pages=3, pause=0)
    assert len(records) == 1
    assert stats["pages"] == 2
    assert (tmp_path / "ui_capture_manifest.json").exists()
    assert len(list((tmp_path / "ui_screenshots").glob("*.png"))) == 2
