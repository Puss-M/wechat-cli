"""Deterministic local reports built from the canonical Moments archive."""

from __future__ import annotations

import html
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
from pathlib import Path


class ReportError(ValueError):
    """The archive cannot produce the requested report."""


def _load_records(archive_dir) -> list[dict]:
    root = Path(archive_dir).expanduser().resolve()
    db_path = root / "moments.sqlite"
    if not db_path.is_file():
        raise ReportError(f"找不到归档数据库: {db_path}")
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT moment_id, created_at_epoch, created_at_local, timezone, time_precision, "
                "text, source, verification, status, identity_confidence, record_json "
                "FROM moments"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ReportError(f"无法读取归档数据库: {exc}") from exc
    records = []
    for row in rows:
        try:
            payload = json.loads(row[10])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload.update({
            "moment_id": row[0],
            "created_at_epoch": row[1],
            "created_at_local": row[2],
            "timezone": row[3],
            "time_precision": row[4],
            "text": row[5],
            "source": row[6],
            "verification": row[7],
            "status": row[8],
            "identity_confidence": row[9],
        })
        records.append(payload)
    return records


def _period(record: dict) -> str | None:
    local = str(record.get("created_at_local") or "")
    if len(local) >= 7 and re.fullmatch(r"\d{4}-\d{2}", local[:7]):
        try:
            datetime.strptime(local[:7], "%Y-%m")
            return local[:7]
        except ValueError:
            pass
    epoch = record.get("created_at_epoch")
    if epoch is None:
        return None
    try:
        return _epoch_datetime(record).strftime("%Y-%m")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _date_label(record: dict) -> str:
    value = str(record.get("created_at_local") or "")
    if value:
        return value
    epoch = record.get("created_at_epoch")
    if epoch is not None:
        try:
            return _epoch_datetime(record).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return "时间未知"


def _epoch_datetime(record: dict) -> datetime:
    epoch = int(record["created_at_epoch"])
    value = str(record.get("timezone") or "").strip()
    tz = timezone.utc
    if value:
        try:
            if value.upper() in {"UTC", "GMT", "Z"}:
                tz = timezone.utc
            elif re.fullmatch(r"[+-]\d{2}:?\d{2}", value):
                sign = 1 if value[0] == "+" else -1
                digits = value[1:].replace(":", "")
                from datetime import timedelta
                tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
            else:
                tz = ZoneInfo(value)
        except Exception:
            pass
    return datetime.fromtimestamp(epoch, tz)


def _images(record: dict) -> list[dict]:
    images = record.get("images")
    return images if isinstance(images, list) else []


def _location_label(record: dict) -> str | None:
    location = record.get("location")
    if not isinstance(location, dict):
        return None
    values = [str(location.get(key) or "").strip() for key in
              ("country", "city", "poiName", "poiAddress", "poiAddressName")]
    label = " · ".join(dict.fromkeys(value for value in values if value))
    return label or None


def _metrics(records: list[dict]) -> dict:
    dated = [record for record in records if _period(record)]
    active_days = {_date_label(record)[:10] for record in dated}
    places = Counter(_location_label(record) for record in dated if _location_label(record))
    tags: Counter[str] = Counter()
    for record in dated:
        annotations = record.get("annotations")
        if isinstance(annotations, dict):
            values = annotations.get("topics", [])
            if isinstance(values, list):
                tags.update(str(value) for value in values if value)
    return {
        "posts": len(records),
        "dated_posts": len(dated),
        "active_days": len(active_days - {""}),
        "images": sum(len(_images(record)) for record in records),
        "links": sum(1 for record in records if record.get("link")),
        "locations": sum(1 for record in records if _location_label(record)),
        "unassigned_time": sum(1 for record in records if not _period(record)),
        "unverified": sum(1 for record in records if record.get("verification") != "xml_author"),
        "incomplete": sum(1 for record in records if record.get("status") != "complete"),
        "missing_media": sum(len(record.get("archive_missing_assets", []))
                             for record in records if isinstance(record.get("archive_missing_assets"), list)),
        "places": places,
        "topics": tags,
    }


def _record_link(record: dict, report_dir: Path, archive_root: Path) -> str | None:
    assets = record.get("archive_assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        relative = asset.get("relative_path")
        if relative:
            target = (archive_root / str(relative)).resolve()
            if not target.is_file() or archive_root not in target.parents:
                return None
            return str(Path(__import__("os").path.relpath(target, report_dir))).replace("\\", "/")
    return None


def _record_lines(records: list[dict], report_dir: Path, archive_root: Path) -> list[str]:
    lines = []
    ordered = sorted(records, key=lambda item: (item.get("created_at_epoch") or 0, item.get("moment_id", "")))
    for record in ordered:
        text = str(record.get("text") or "").replace("\n", " ").strip() or "（无文字）"
        if len(text) > 180:
            text = text[:180] + "..."
        suffix = []
        place = _location_label(record)
        if place:
            suffix.append(f"地点：{place}")
        media_link = _record_link(record, report_dir, archive_root)
        if media_link:
            suffix.append(f"媒体：[{media_link}]({media_link})")
        suffix.append(f"可信度：{record.get('verification', 'unknown')}")
        lines.append(f"- **{_date_label(record)}** {text}（{'；'.join(suffix)}）")
    return lines


def _coverage_lines(metrics: dict) -> list[str]:
    return [
        f"- 可按年月归属：{metrics['dated_posts']} 条",
        f"- 时间无法归属：{metrics['unassigned_time']} 条",
        f"- 未通过 XML 作者验证：{metrics['unverified']} 条",
        f"- 标记为不完整：{metrics['incomplete']} 条",
        f"- 缺失媒体：{metrics.get('missing_media', 0)} 项",
    ]


def build_monthly_report(archive_dir, period: str) -> dict:
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError:
        raise ReportError("月份必须使用 YYYY-MM 格式")
    root = Path(archive_dir).expanduser().resolve()
    all_records = _load_records(root)
    records = [record for record in all_records if _period(record) == period]
    metrics = _metrics(records)
    return {"kind": "monthly", "period": period, "records": records, "metrics": metrics,
            "coverage": _metrics(all_records), "total_records": len(all_records)}


def build_yearly_report(archive_dir, year: str) -> dict:
    if len(year) != 4 or not year.isdigit() or int(year) == 0:
        raise ReportError("年份必须使用 YYYY 格式")
    root = Path(archive_dir).expanduser().resolve()
    all_records = _load_records(root)
    records = [record for record in all_records if (_period(record) or "")[:4] == year]
    monthly = Counter(_period(record) for record in records if _period(record))
    month_counts = {f"{year}-{month:02d}": monthly.get(f"{year}-{month:02d}", 0) for month in range(1, 13)}
    return {"kind": "yearly", "period": year, "records": records, "metrics": _metrics(records),
            "coverage": _metrics(all_records),
            "monthly": month_counts, "total_records": len(all_records)}


def build_journey_report(archive_dir) -> dict:
    root = Path(archive_dir).expanduser().resolve()
    records = _load_records(root)
    ordered = sorted(records, key=lambda item: (item.get("created_at_epoch") or 0, item.get("moment_id", "")))
    places = Counter(_location_label(record) for record in ordered if _location_label(record))
    return {"kind": "journey", "period": "all", "records": ordered, "metrics": _metrics(records),
            "coverage": _metrics(records),
            "places": dict(places), "total_records": len(records)}


def render_report(report: dict, archive_dir, output_path, fmt: str) -> Path:
    root = Path(archive_dir).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    title = {"monthly": f"{report['period']} 月度小记", "yearly": f"{report['period']} 年度报告",
             "journey": "我的朋友圈回忆旅程"}[report["kind"]]
    lines = [f"# {title}", "", f"归档记录：{report['total_records']} 条；本报告：{metrics['posts']} 条。", "",
             "## 统计", "", f"- 活跃天数：{metrics['active_days']}", f"- 图片：{metrics['images']} 张",
             f"- 链接：{metrics['links']} 条", f"- 地点：{metrics['locations']} 条", "", "## 数据覆盖", ""]
    lines.extend(_coverage_lines(report.get("coverage", metrics)))
    if report["kind"] == "yearly":
        lines.extend(["", "## 月度趋势", ""])
        lines.extend(f"- {month}：{count} 条" + ("（暂无归档记录或未覆盖）" if count == 0 else "")
                     for month, count in report["monthly"].items())
    lines.extend(["", "## 地点", ""])
    for place, count in metrics["places"].most_common(12):
        lines.append(f"- {place}：{count} 条")
    lines.extend(["", "## 时间线", ""])
    report_dir = target.parent
    lines.extend(_record_lines(report["records"], report_dir, root))
    markdown = "\n".join(lines).rstrip() + "\n"
    if fmt == "markdown":
        target.write_text(markdown, encoding="utf-8", newline="\n")
    else:
        rendered = []
        for line in markdown.splitlines():
            escaped = html.escape(line)
            escaped = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
            if escaped.startswith("# "):
                rendered.append(f"<h1>{escaped[2:]}</h1>")
            elif escaped.startswith("## "):
                rendered.append(f"<h2>{escaped[3:]}</h2>")
            elif escaped.startswith("- "):
                rendered.append(f"<p>{escaped}</p>")
            elif escaped:
                rendered.append(f"<p>{escaped}</p>")
        body = "\n".join(rendered)
        html_doc = f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;line-height:1.7;color:#222}}h1{{line-height:1.2}}a{{color:#06c}}</style><main><div>{body}</div></main>"
        target.write_text(html_doc + "\n", encoding="utf-8", newline="\n")
    return target
