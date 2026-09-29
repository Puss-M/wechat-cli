"""Best-effort local WeChat desktop UI capture for uncached Moments.

The UI path is deliberately additive: it stores screenshots as evidence and
never treats a missing accessibility label as proof that a post is absent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path


class UiCaptureError(RuntimeError):
    """The WeChat window could not be captured safely."""


@dataclass(frozen=True)
class UiMoment:
    id: str
    text: str
    time_label: str | None
    urls: list[str]
    screenshot: str
    page: int

    def as_record(self):
        link = {"url": self.urls[0], "title": None, "description": None} if self.urls else None
        return {
            "id": self.id,
            "time": self.time_label,
            "text": self.text,
            "images": [{"screenshot": self.screenshot}],
            "media": [],
            "location": None,
            "link": link,
            "capture_source": "wechat_ui",
            "capture_page": self.page,
            "capture_screenshot": self.screenshot,
        }


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?:20\d{2}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}(?:日)?|"
    r"\d{1,2}[-/.月]\s*\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?)"
)


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fingerprint(text, time_label, urls):
    payload = "\n".join([_clean_text(time_label), _clean_text(text), *sorted(urls)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _date_key(value):
    text = _clean_text(value)
    match = re.search(r"(20\d{2})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})", text)
    if match:
        return "-".join(str(int(part)).zfill(2) for part in match.groups())
    match = re.search(r"(\d{1,2})[-/.月]\s*(\d{1,2})", text)
    if match:
        return "-".join(str(int(part)).zfill(2) for part in match.groups())
    return text


def _visible_lines(window):
    lines = []
    try:
        controls = window.descendants()
    except Exception as exc:  # UIA implementations differ by WeChat build.
        raise UiCaptureError(f"无法读取微信窗口控件树: {exc}") from exc
    for control in controls:
        text = ""
        try:
            if not control.is_visible():
                continue
            text = _clean_text(control.window_text())
        except Exception:
            text = ""
        if text and text not in lines:
            lines.append(text)
    return lines


def parse_visible_moments(lines, screenshot, page):
    """Parse conservative post candidates from visible UI labels.

    A date label starts a candidate. If WeChat exposes no date labels, keep the
    entire page as one screenshot-backed candidate instead of inventing splits.
    """
    cleaned = [_clean_text(line) for line in lines if _clean_text(line)]
    groups = []
    current = []
    current_time = None
    for line in cleaned:
        match = _DATE_RE.search(line)
        if match:
            if current:
                groups.append((current_time, current))
            current = []
            current_time = match.group(0)
            current.append(line)
        elif current:
            current.append(line)
    if current:
        groups.append((current_time, current))
    if not groups and cleaned:
        groups = [(None, cleaned)]

    result = []
    for time_label, group in groups:
        urls = sorted(set(url.rstrip(".,，。") for line in group for url in _URL_RE.findall(line)))
        text = "\n".join(
            _URL_RE.sub("", line).strip() for line in group
        ).strip()
        if not text and not urls:
            continue
        result.append(UiMoment(
            id=_fingerprint(text, time_label, urls),
            text=text,
            time_label=time_label,
            urls=urls,
            screenshot=screenshot,
            page=page,
        ))
    return result


def _find_window(title_pattern):
    try:
        from pywinauto import Desktop  # type: ignore[import-not-found,import-untyped]
    except ImportError as exc:
        raise UiCaptureError(
            "UI 采集需要可选依赖 pywinauto；请安装: python -m pip install pywinauto"
        ) from exc
    try:
        windows = Desktop(backend="uia").windows(visible_only=True)
    except Exception as exc:
        raise UiCaptureError(f"无法枚举桌面窗口: {exc}") from exc
    pattern = re.compile(title_pattern, re.IGNORECASE)
    matches = [window for window in windows if pattern.search(_clean_text(window.window_text()))]
    if not matches:
        raise UiCaptureError(f"没有找到微信窗口（标题匹配: {title_pattern}）")
    if len(matches) > 1:
        titles = ", ".join(_clean_text(item.window_text()) for item in matches[:5])
        raise UiCaptureError(f"找到多个微信窗口，请用 --ui-window-title 精确指定：{titles}")
    return matches[0]


def collect_visible_moments(
    destination,
    max_pages=100,
    pause=1.2,
    title_pattern=r"微信|WeChat|Weixin",
):
    """Capture visible WeChat pages until scrolling stops producing new text."""
    if max_pages < 1:
        raise UiCaptureError("--ui-max-pages 必须大于 0")
    try:
        import pyautogui  # type: ignore[import-untyped]
    except ImportError as exc:
        raise UiCaptureError(
            "UI 采集需要可选依赖 pyautogui；请安装: python -m pip install pyautogui"
        ) from exc

    root = Path(destination).resolve()
    screenshot_dir = root / "ui_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    window = _find_window(title_pattern)
    try:
        window.restore()
        window.set_focus()
    except Exception as exc:
        raise UiCaptureError(f"无法激活微信窗口: {exc}") from exc
    try:
        rect = window.rectangle()
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
    except Exception as exc:
        raise UiCaptureError(f"无法定位微信窗口滚动区域: {exc}") from exc

    records = {}
    page_fingerprints = set()
    snapshots = []
    manifest = root / "ui_capture_manifest.json"

    def write_manifest():
        manifest.write_text(json.dumps({
            "source": "wechat_desktop_ui",
            "pages": len(snapshots),
            "records": len(records),
            "screenshots": snapshots,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for page in range(1, max_pages + 1):
        time.sleep(max(0.0, pause))
        lines = _visible_lines(window)
        page_text = "\n".join(lines)
        page_fingerprint = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
        screenshot = screenshot_dir / f"page-{page:04d}.png"
        try:
            image = pyautogui.screenshot()
            image.crop((
                max(0, rect.left), max(0, rect.top),
                min(image.width, rect.right), min(image.height, rect.bottom),
            )).save(screenshot)
        except Exception as exc:
            raise UiCaptureError(f"无法保存第 {page} 页截图: {exc}") from exc
        snapshots.append({"page": page, "screenshot": str(screenshot), "lines": lines})
        for item in parse_visible_moments(lines, str(screenshot), page):
            records[item.id] = item.as_record()
        write_manifest()
        if page_fingerprint in page_fingerprints:
            break
        page_fingerprints.add(page_fingerprint)
        pyautogui.moveTo(x, y, duration=0.1)
        pyautogui.scroll(-6)

    return list(records.values()), {
        "pages": len(snapshots),
        "records": len(records),
        "manifest": str(manifest),
    }


def merge_moment_records(local_records, ui_records):
    """Merge UI evidence without replacing richer local XML metadata."""
    merged = list(local_records)
    by_key = {
        (_date_key(item.get("time")), _clean_text(item.get("text"))): index
        for index, item in enumerate(merged)
    }
    for item in ui_records:
        key = (_date_key(item.get("time")), _clean_text(item.get("text")))
        existing = by_key.get(key)
        if existing is None or not key[1]:
            merged.append(item)
            by_key[key] = len(merged) - 1
            continue
        current = merged[existing]
        current.setdefault("ui_captures", []).append({
            "page": item.get("capture_page"),
            "screenshot": item.get("capture_screenshot"),
        })
    return merged
