"""Read the current account's cached Moments from sns.db."""

import sqlite3
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MomentDataError(ValueError):
    """The cached Moments data cannot be exported without losing records."""


class MomentImageDownloadError(ValueError):
    """A local copy of one or more own Moment images could not be fetched."""


def _local_name(tag):
    """Return an XML tag name without a namespace prefix."""
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _find_first(element, name):
    for candidate in element.iter():
        if _local_name(candidate.tag) == name:
            return candidate
    return None


def _find_text(element, name):
    child = _find_first(element, name)
    return (child.text or "").strip() if child is not None and child.text else ""


def _direct_texts(element, name):
    """Return direct-child text values for fields with strict ownership semantics."""
    values = []
    for child in list(element):
        if _local_name(child.tag) == name:
            values.append((child.text or "").strip())
    return values


def _get_attr(element, name):
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return (value or "").strip()
    return ""


class _HttpOnlyRedirectHandler(HTTPRedirectHandler):
    """Reject redirects outside HTTP(S), including file and custom schemes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in {"http", "https"}:
            raise MomentImageDownloadError("图片地址重定向到不支持的协议")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _parse_location(timeline):
    location = _find_first(timeline, "location")
    if location is None:
        return None

    fields = (
        "country", "city", "poiName", "poiAddress", "poiAddressName",
        "latitude", "longitude",
    )
    details = {field: _get_attr(location, field) for field in fields}
    details = {field: value for field, value in details.items() if value}
    labels = {"country", "city", "poiName", "poiAddress", "poiAddressName"}
    has_coordinates = False
    try:
        has_coordinates = (
            float(details.get("latitude", "0")) != 0
            or float(details.get("longitude", "0")) != 0
        )
    except ValueError:
        has_coordinates = bool(details.get("latitude") or details.get("longitude"))
    if not labels.intersection(details) and not has_coordinates:
        return None
    return details


def _parse_media(timeline):
    content_object = _find_first(timeline, "ContentObject")
    if content_object is None:
        return [], []

    media_items = []
    images = []
    media_list = _find_first(content_object, "mediaList")
    if media_list is None:
        return [], []
    for item in media_list:
        if _local_name(item.tag) != "media":
            continue
        media_type = _find_text(item, "type") or _find_text(item, "mediaType")
        url = _find_text(item, "url")
        thumbnail = _find_text(item, "thumb") or _find_text(item, "thumbUrl")
        kind = "image" if media_type == "2" else "other"
        media = {
            "id": _find_text(item, "id") or None,
            "type": media_type,
            "kind": kind,
            "url": url or None,
            "thumbnail_url": thumbnail or None,
            "title": _find_text(item, "title") or None,
            "description": _find_text(item, "description") or None,
            "size": _find_text(item, "size") or None,
        }
        media_items.append(media)
        if kind == "image":
            images.append({
                "id": media["id"],
                "url": media["url"],
                "thumbnail_url": media["thumbnail_url"],
            })

    return media_items, images


def _parse_link(timeline):
    content_object = _find_first(timeline, "ContentObject")
    if content_object is None:
        return None
    link = {
        "url": _find_text(content_object, "contentUrl") or None,
        "title": _find_text(content_object, "title") or None,
        "description": _find_text(content_object, "description") or None,
    }
    return link if any(link.values()) else None


def load_own_moments(
    db_path,
    self_username,
    include_empty=False,
    strict=False,
    return_diagnostics=False,
):
    if not self_username:
        raise MomentDataError("无法确认当前账号 ID，已停止导出")

    moments_with_time = []
    skipped_empty = 0
    skipped_records = []
    candidate_count = matched_author_count = 0
    seen_ids = set()
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT tid, user_name, content FROM SnsTimeLine ORDER BY tid",
        )
        for tid, _row_username, raw_xml in rows:
            candidate_count += 1
            try:
                root = ET.fromstring(raw_xml)
                timeline = root if _local_name(root.tag) == "TimelineObject" else _find_first(root, "TimelineObject")
                if timeline is None:
                    raise ValueError("缺少 TimelineObject")
                authors = _direct_texts(timeline, "username")
                if len(authors) != 1 or not authors[0]:
                    raise ValueError("记录缺少直接 XML 作者")
                xml_username = authors[0]
                if xml_username != self_username:
                    raise ValueError("记录作者与当前账号不一致")
                matched_author_count += 1
                moment_id = _find_text(timeline, "id") or _get_attr(timeline, "id")
                if not moment_id or moment_id in seen_ids:
                    raise ValueError("帖子 ID 缺失或重复")
                raw_created = _find_text(timeline, "createTime")
                created = int(float(raw_created))
                if created >= 10**12:
                    created //= 1000
                if created <= 0:
                    raise ValueError("发布时间无效")
                timestamp = datetime.fromtimestamp(created).astimezone().isoformat(timespec="seconds")
            except (ET.ParseError, TypeError, ValueError, OverflowError, OSError) as exc:
                reason = str(exc)
                if strict:
                    raise MomentDataError(f"朋友圈记录 tid={tid} 无法可靠解析: {reason}") from exc
                skipped_records.append({"tid": tid, "reason": reason})
                continue

            content = _find_text(timeline, "contentDesc")
            media, images = _parse_media(timeline)
            location = _parse_location(timeline)
            link = _parse_link(timeline)
            if not content.strip() and not (media or location or link) and not include_empty:
                skipped_empty += 1
                continue
            seen_ids.add(moment_id)
            moments_with_time.append((created, {
                "id": moment_id,
                "time": timestamp,
                "text": content,
                "images": images,
                "media": media,
                "location": location,
                "link": link,
            }))

    moments_with_time.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)
    moments = [moment for _, moment in moments_with_time]
    if not return_diagnostics:
        return moments, skipped_empty
    diagnostics = {
        "candidate_count": candidate_count,
        "matched_author_count": matched_author_count,
        "exported_count": len(moments),
        "skipped_empty": skipped_empty,
        "skipped_invalid": len(skipped_records),
        "skipped_records": skipped_records,
    }
    return moments, skipped_empty, diagnostics


def _image_extension(data, content_type=""):
    signatures = (
        (b"\xff\xd8\xff", ".jpg"),
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"RIFF", ".webp"),
    )
    for signature, extension in signatures:
        if data.startswith(signature):
            return extension
    content_type = content_type.split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type, ".bin")


def _fetch_image(url, timeout=30, max_bytes=50 * 1024 * 1024):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MomentImageDownloadError("图片地址不是受支持的 HTTP(S) 地址")
    request = Request(url, headers={"User-Agent": "wechat-cli/0.2"})
    try:
        opener = build_opener(_HttpOnlyRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise MomentImageDownloadError("图片超过 50 MB 限制")
            chunks = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise MomentImageDownloadError("图片超过 50 MB 限制")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get_content_type()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, MomentImageDownloadError):
            raise
        raise MomentImageDownloadError(f"图片请求失败: {exc}") from exc


def download_own_moment_images(moments, destination, timeout=30, limit=0):
    """Download only image URLs already attached to verified own Moment XML rows.

    The cache directory is deliberately not consulted: it can contain images
    from other accounts' Moments that the current account happened to view.
    """
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    downloaded = reused = failed = attempted = 0
    for moment in moments:
        moment_dir = root / "moments" / str(moment["id"])
        for index, image in enumerate(moment.get("images", []), start=1):
            if limit and attempted >= limit:
                return {"downloaded": downloaded, "reused": reused, "failed": failed}
            attempted += 1
            url = image.get("url") or image.get("thumbnail_url")
            if not url:
                image["download_error"] = "朋友圈记录没有图片地址"
                failed += 1
                continue
            try:
                if not moment_dir.exists():
                    moment_dir.mkdir(parents=True, exist_ok=True)
                data, content_type = _fetch_image(url, timeout=timeout)
                extension = _image_extension(data, content_type)
                target = moment_dir / f"{index:02d}{extension}"
                if target.exists():
                    reused += 1
                else:
                    temporary = target.with_suffix(target.suffix + ".part")
                    temporary.write_bytes(data)
                    temporary.replace(target)
                    downloaded += 1
                image["local_path"] = str(target)
                image["local_size"] = len(data)
            except (MomentImageDownloadError, OSError) as exc:
                image["download_error"] = str(exc)
                failed += 1
    return {"downloaded": downloaded, "reused": reused, "failed": failed}


def render_markdown(moments, skipped_empty, diagnostics=None):
    diagnostics = diagnostics or {}
    lines = [
        "# 我的朋友圈（本机缓存）", "",
        f"导出记录：{len(moments)} 条；跳过无内容记录：{skipped_empty} 条；"
        f"跳过异常记录：{diagnostics.get('skipped_invalid', 0)} 条。", "",
        "仅包含当前电脑微信缓存中的帖子，不代表账号全部历史。", "",
    ]
    skipped_records = diagnostics.get("skipped_records", [])
    if skipped_records:
        lines.extend(["## 导出诊断", "", "以下记录未导出，原因已保留供排查：", ""])
        for item in skipped_records:
            lines.append(f"- tid={item.get('tid')}：{item.get('reason', '未知原因')}")
        lines.append("")
    for moment in moments:
        lines.extend([
            f"## {moment['time']}", "",
            f"帖子 ID：`{moment['id']}`", "",
            moment["text"] or "（无文字）", "",
        ])
        location = moment.get("location")
        if location:
            place = "、".join(location.get(key, "") for key in ("country", "city", "poiName", "poiAddress", "poiAddressName") if location.get(key))
            coordinates = ", ".join(location.get(key, "") for key in ("latitude", "longitude") if location.get(key))
            lines.extend([f"位置：{place or '未命名地点'}", f"坐标：{coordinates}" if coordinates else "", ""])
        link = moment.get("link")
        if link:
            lines.extend([f"链接：{link.get('title') or link.get('url') or '分享链接'}", link.get("url") or "", link.get("description") or "", ""])
        if moment.get("images"):
            lines.append("图片：")
            for image in moment["images"]:
                lines.append(f"- 原图：{image.get('url') or ''}")
                if image.get("thumbnail_url"):
                    lines.append(f"  缩略图：{image['thumbnail_url']}")
                if image.get("local_path"):
                    lines.append(f"  本地文件：{image['local_path']}")
                if image.get("download_error"):
                    lines.append(f"  下载失败：{image['download_error']}")
            lines.append("")
        if moment.get("capture_screenshot"):
            lines.extend([f"界面截图证据：{moment['capture_screenshot']}", ""])
        for capture in moment.get("ui_captures", []):
            if capture.get("screenshot"):
                lines.extend([f"界面截图证据：{capture['screenshot']}", ""])
    return "\n".join(lines).rstrip() + "\n"
