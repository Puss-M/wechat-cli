"""Decode WeChat 4.x V2 image cache files locally.

The cache format is shared by ``cache/YYYY-MM/Sns/Img`` and chat image
attachments.  This module never touches the source cache: callers receive
decoded bytes and decide where to write them.
"""

from __future__ import annotations

import hashlib
import re
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# PyCryptodome supplies this namespace; it is declared in pyproject.toml.
from Crypto.Cipher import AES  # nosec B413


V1_SIGNATURE = b"\x07\x08V1\x08\x07"
V2_SIGNATURE = b"\x07\x08V2\x08\x07"
V1_KEY = b"cfcd208495d565ef"

_MAGICS = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),
    (b"wxgf", ".wxgf"),
    (b"WXGF", ".wxgf"),
)


class ImageDecodeError(ValueError):
    """Raised when a cache file cannot be verified as an image."""


@dataclass(frozen=True)
class CacheImage:
    path: Path
    month: str
    size: int
    signature: str


@dataclass(frozen=True)
class DecodedImage:
    data: bytes
    extension: str
    xor_key: int
    version: str


def image_extension(data: bytes) -> str | None:
    """Return an image extension only for known file signatures."""
    for signature, extension in _MAGICS:
        if signature == b"RIFF":
            if len(data) >= 12 and data[8:12] == b"WEBP":
                return extension
            continue
        if data.startswith(signature):
            return extension
    return None


def _aligned_aes_size(size: int) -> int:
    # WeChat appends a complete PKCS#7 block when size is already aligned.
    return size + (16 - size % 16) if size % 16 else size + 16


def _valid_tail(data: bytes, extension: str) -> bool:
    if extension == ".jpg":
        return data.endswith(b"\xff\xd9")
    if extension == ".png":
        return data.endswith(b"IEND\xaeB`\x82")
    if extension == ".gif":
        return data.endswith(b";")
    return True


def _jpeg_end(data: bytes) -> int | None:
    """Find JPEG EOI while skipping length-delimited segments and scan data."""
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            return None
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            return offset
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return None
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        offset += segment_length
        if marker != 0xDA:
            continue

        # In entropy-coded data, FF00 is a stuffed byte and D0-D7 are restart
        # markers. Any other marker starts the next JPEG segment.
        while offset < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker_start = offset
            offset += 1
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return None
            scan_marker = data[offset]
            offset += 1
            if scan_marker == 0x00 or 0xD0 <= scan_marker <= 0xD7:
                continue
            if scan_marker == 0xD9:
                return offset
            offset = marker_start
            break
    return None


def _normalize_image_bytes(data: bytes, extension: str) -> bytes | None:
    """Drop cache bytes appended after a complete image payload.

    Some WeChat V2 cache entries contain a valid JPEG followed by an opaque
    cache tail.  The image decoder should return the image payload only while
    still requiring a real format terminator.
    """
    if extension == ".jpg":
        end = _jpeg_end(data)
        return data[:end] if end is not None else None
    if extension == ".png":
        offset = 8
        while offset + 12 <= len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            chunk_type = data[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(data):
                return None
            expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
            if zlib.crc32(data[offset + 4:offset + 8 + length]) & 0xFFFFFFFF != expected_crc:
                return None
            if chunk_type == b"IEND" and length == 0:
                return data[:end]
            offset = end
        return None
    if extension == ".gif":
        if len(data) < 13:
            return None
        offset = 13
        screen_flags = data[10]
        if screen_flags & 0x80:
            offset += 3 * (1 << ((screen_flags & 0x07) + 1))
        if offset > len(data):
            return None
        while offset < len(data):
            block_type = data[offset]
            offset += 1
            if block_type == 0x3B:
                return data[:offset]
            if block_type == 0x21:
                if offset >= len(data):
                    return None
                offset += 1  # Extension label; payload follows as sub-blocks.
            elif block_type == 0x2C:
                if offset + 9 > len(data):
                    return None
                image_flags = data[offset + 8]
                offset += 9
                if image_flags & 0x80:
                    offset += 3 * (1 << ((image_flags & 0x07) + 1))
                if offset >= len(data):
                    return None
                offset += 1  # LZW minimum code size.
            else:
                return None
            while offset < len(data):
                block_size = data[offset]
                offset += 1
                if block_size == 0:
                    break
                offset += block_size
            else:
                return None
            if offset > len(data):
                return None
        return None
    if extension == ".webp":
        if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return None
        size = struct.unpack_from("<I", data, 4)[0] + 8
        if size < 20 or size > len(data):
            return None
        offset = 12
        has_image_chunk = False
        while offset + 8 <= size:
            chunk_type = data[offset:offset + 4]
            chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
            chunk_end = offset + 8 + chunk_size + (chunk_size & 1)
            if chunk_end > size:
                return None
            if chunk_type in {b"VP8 ", b"VP8L", b"VP8X"}:
                minimum = 10 if chunk_type in {b"VP8 ", b"VP8X"} else 5
                if chunk_size < minimum:
                    return None
                has_image_chunk = True
            offset = chunk_end
        if offset == size and has_image_chunk:
            return data[:size]
        return None
    return data


def _verified_image(data: bytes) -> tuple[bytes, str] | None:
    extension = image_extension(data)
    if not extension:
        return None
    normalized = _normalize_image_bytes(data, extension)
    if normalized is None or not _valid_tail(normalized, extension):
        return None
    return normalized, extension


def _xor_candidates(value: int | None) -> Iterator[int]:
    if value is not None:
        yield value & 0xFF
        return
    # Common values first; all values are still checked against image tails.
    yield 0xF4
    yield from (key for key in range(256) if key != 0xF4)


def decode_v2(data: bytes, aes_key: bytes | str, xor_key: int | None = None) -> DecodedImage:
    """Decode and verify one V2 file.

    ``aes_key`` is the account image key, not the SQLCipher database key. It
    must be exactly 16 bytes (the upstream format uses an ASCII key).
    """
    if not data.startswith(V2_SIGNATURE):
        raise ImageDecodeError("不是微信 V2 图片缓存")
    if len(data) < 15:
        raise ImageDecodeError("V2 文件头不完整")
    key = aes_key.encode("ascii") if isinstance(aes_key, str) else bytes(aes_key)
    if len(key) != 16:
        raise ImageDecodeError("图片 AES 密钥必须正好 16 字节")

    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    aligned = _aligned_aes_size(aes_size)
    start = 15
    end = start + aligned
    if end > len(data) or xor_size > len(data) - end:
        raise ImageDecodeError("V2 分段长度越界")

    try:
        decrypted = AES.new(key, AES.MODE_ECB).decrypt(data[start:end])
    except ValueError as exc:
        raise ImageDecodeError(f"AES 解码失败: {exc}") from exc
    pad = decrypted[-1] if decrypted else 0
    if 1 <= pad <= 16 and decrypted.endswith(bytes([pad]) * pad):
        decrypted = decrypted[:-pad]

    raw_end = len(data) - xor_size if xor_size else len(data)
    plain = decrypted + data[end:raw_end]
    tail = data[raw_end:] if xor_size else b""
    for candidate in _xor_candidates(xor_key):
        decoded = plain + bytes(byte ^ candidate for byte in tail)
        verified = _verified_image(decoded)
        if verified is not None:
            normalized, extension = verified
            return DecodedImage(normalized, extension, candidate, "v2")
    raise ImageDecodeError("AES/XOR 解码后未通过图片签名和尾部校验")


def decode_v1(data: bytes, xor_key: int | None = None) -> DecodedImage:
    """Decode V1 with WeChat's fixed AES key."""
    if not data.startswith(V1_SIGNATURE):
        raise ImageDecodeError("不是微信 V1 图片缓存")
    if len(data) < 15:
        raise ImageDecodeError("V1 文件头不完整")
    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    aligned = _aligned_aes_size(aes_size)
    start = 15
    end = start + aligned
    if end > len(data) or xor_size > len(data) - end:
        raise ImageDecodeError("V1 分段长度越界")
    decrypted = AES.new(V1_KEY, AES.MODE_ECB).decrypt(data[start:end])
    pad = decrypted[-1] if decrypted else 0
    if 1 <= pad <= 16 and decrypted.endswith(bytes([pad]) * pad):
        decrypted = decrypted[:-pad]
    raw_end = len(data) - xor_size if xor_size else len(data)
    plain = decrypted + data[end:raw_end]
    tail = data[raw_end:] if xor_size else b""
    for candidate in _xor_candidates(xor_key):
        decoded = plain + bytes(byte ^ candidate for byte in tail)
        verified = _verified_image(decoded)
        if verified is not None:
            normalized, extension = verified
            return DecodedImage(normalized, extension, candidate, "v1")
    raise ImageDecodeError("V1 解码后未通过图片签名和尾部校验")


def decode_legacy(data: bytes) -> DecodedImage:
    """Decode the pre-V2 single-byte XOR format when its header proves it."""
    if data.startswith(V1_SIGNATURE):
        raise ImageDecodeError("V1 需要按客户端版本单独解析，当前未启用猜测式解码")
    for key in range(256):
        decoded = bytes(byte ^ key for byte in data)
        verified = _verified_image(decoded)
        if verified is not None:
            normalized, extension = verified
            return DecodedImage(normalized, extension, key, "legacy-xor")
    raise ImageDecodeError("未识别的旧版 XOR 图片缓存")


def decode_cache_file(path: str | os.PathLike[str], aes_key: bytes | str | None = None,
                      xor_key: int | None = None) -> DecodedImage:
    data = Path(path).read_bytes()
    if data.startswith(V2_SIGNATURE):
        if aes_key is None:
            raise ImageDecodeError("V2 图片需要 --image-key-file 提供 16 字节图片密钥")
        return decode_v2(data, aes_key, xor_key)
    if data.startswith(V1_SIGNATURE):
        return decode_v1(data, xor_key)
    return decode_legacy(data)


def iter_sns_images(
    db_dir: str | os.PathLike[str], allowed_names: set[str] | None = None
) -> Iterator[CacheImage]:
    """Yield cache files, optionally filtering by name before opening them."""
    account_dir = Path(db_dir).resolve().parent
    cache_root = account_dir / "cache"
    if not cache_root.is_dir():
        return
    for path in cache_root.glob("*/Sns/Img/*/*"):
        if allowed_names is not None and (
            not re.fullmatch(r"[0-9a-f]{30}", path.name)
            or path.name.lower() not in allowed_names
        ):
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                signature = handle.read(6).hex()
        except OSError:
            continue
        yield CacheImage(path, path.parents[3].name, size, signature)


def own_sns_cache_names(moment_id: str, media_id: str) -> set[str]:
    """Return the two verified WeChat cache names for one own Moment image."""
    if not moment_id or not media_id:
        return set()
    return {
        hashlib.md5(
            f"{moment_id}_{media_id}_{variant}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[2:]
        for variant in ("1", "2")
    }


def iter_own_sns_images(db_dir: str | os.PathLike[str], moments) -> Iterator[CacheImage]:
    """Yield only cache files whose names are derived from verified own posts."""
    wanted = set()
    for moment in moments:
        for image in moment.get("images", []):
            wanted.update(own_sns_cache_names(str(moment.get("id") or ""), str(image.get("id") or "")))
    if not wanted:
        return
    yield from iter_sns_images(db_dir, allowed_names=wanted)


def output_name(source: Path, data: bytes, extension: str) -> str:
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f"{source.name}-{digest}{extension}"


def read_image_key(path: str | os.PathLike[str]) -> bytes:
    """Read a local 16-byte image key without logging or echoing it."""
    raw = Path(path).read_bytes().strip()
    if len(raw) == 16:
        return raw
    try:
        decoded = bytes.fromhex(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImageDecodeError("图片密钥文件应包含 16 字节 ASCII 或 32 位十六进制") from exc
    if len(decoded) != 16:
        raise ImageDecodeError("图片密钥文件中的十六进制值必须是 32 个字符")
    return decoded


def normalize_image_key(value: str | bytes) -> bytes:
    """Normalize a locally supplied image key without exposing its value."""
    raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
    if len(raw) == 16:
        return raw
    try:
        decoded = bytes.fromhex(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImageDecodeError("图片 AES 密钥不是 16 字节 ASCII 或 32 位十六进制") from exc
    if len(decoded) != 16:
        raise ImageDecodeError("图片 AES 密钥的十六进制值必须是 32 个字符")
    return decoded


def _clean_wxid(account_id: str) -> str:
    """Remove the data-directory disambiguator from a Windows wxid."""
    match = re.fullmatch(r"(wxid_[^_]+)(?:_.+)", account_id, flags=re.IGNORECASE)
    return match.group(1) if match else account_id


def _iter_kvcomm_dirs() -> Iterator[Path]:
    roots = []
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value) / "Tencent" / "xwechat")
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("*/kvcomm", "radium/*/kvcomm", "radium/*/*/kvcomm", "kvcomm"):
            for path in root.glob(pattern):
                if path.is_dir() and path not in seen:
                    seen.add(path)
                    yield path


def _iter_kvcomm_codes() -> Iterator[tuple[int, Path]]:
    pattern = re.compile(r"^key_(\d+)_.+\.statistic$", re.IGNORECASE)
    seen = set()
    for directory in _iter_kvcomm_dirs():
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            match = pattern.fullmatch(name)
            if not match:
                continue
            code = int(match.group(1))
            if 0 < code <= 0xFFFFFFFF and code not in seen:
                seen.add(code)
                yield code, directory / name


def _probe_v2_key(path: Path, key: bytes) -> bool:
    """Check one candidate by decrypting only its first AES block."""
    try:
        with path.open("rb") as handle:
            header = handle.read(15 + 16)
        if len(header) < 31 or not header.startswith(V2_SIGNATURE):
            return False
        plain = AES.new(key, AES.MODE_ECB).decrypt(header[15:31])
    except (OSError, ValueError):
        return False
    return image_extension(plain) is not None


def _discover_kvcomm_image_key(db_dir: str | os.PathLike[str], moments) -> tuple[bytes, int | None] | None:
    account_id = Path(db_dir).resolve().parent.name
    wxid = _clean_wxid(account_id)
    samples = []
    for item in iter_own_sns_images(db_dir, moments):
        try:
            with item.path.open("rb") as handle:
                if handle.read(6) == V2_SIGNATURE:
                    samples.append(item.path)
        except OSError:
            continue
        if len(samples) >= 24:
            break
    if not samples:
        return None
    for code, _source in _iter_kvcomm_codes():
        key = hashlib.md5(
            f"{code}{wxid}".encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:16].encode("ascii")
        if any(_probe_v2_key(path, key) for path in samples):
            return key, code & 0xFF
    return None


def discover_local_image_key(db_dir: str | os.PathLike[str], moments) -> tuple[bytes, int | None]:
    """Derive the V2 image key from local kvcomm metadata, with wx_key fallback."""
    derived = _discover_kvcomm_image_key(db_dir, moments)
    if derived is not None:
        return derived
    try:
        import json
        import wx_key  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImageDecodeError(
            "未安装可选的 wx_key；请改用 --image-key-file，或按文档安装本地免费扩展"
        ) from exc
    try:
        payload = json.loads(wx_key.get_image_key())
    except Exception as exc:  # native extension errors vary by version
        raise ImageDecodeError("wx_key 无法从本机配置推导图片密钥") from exc
    account = Path(db_dir).resolve().parent.name
    account_base = account.rsplit("_", 1)[0]
    candidates = payload.get("accounts", []) if isinstance(payload, dict) else []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        wxid = str(item.get("wxid") or "")
        if wxid not in {account, account_base}:
            continue
        keys = item.get("keys", [])
        if not isinstance(keys, list):
            continue
        for candidate in keys:
            if not isinstance(candidate, dict) or candidate.get("aesKey") is None:
                continue
            xor_raw = candidate.get("xorKey")
            try:
                xor = int(str(xor_raw), 0) if xor_raw is not None else None
            except ValueError:
                xor = None
            if xor is not None and not 0 <= xor <= 255:
                continue
            return normalize_image_key(str(candidate["aesKey"])), xor
    raise ImageDecodeError("wx_key 未找到当前微信账号的图片密钥")
