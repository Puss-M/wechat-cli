import base64
import hashlib
import struct
import zlib
from pathlib import Path

from Crypto.Cipher import AES
import pytest

from wechat_cli.core.image_cache import (
    ImageDecodeError,
    decode_cache_file,
    decode_v2,
    discover_local_image_key,
    image_extension,
    iter_own_sns_images,
    own_sns_cache_names,
)


def _v2_blob(image: bytes, key: bytes, xor: int) -> bytes:
    aes_size = len(image) - 2
    aes_plain = image[:aes_size]
    pad = 16 - (len(aes_plain) % 16)
    encrypted = AES.new(key, AES.MODE_ECB).encrypt(aes_plain + bytes([pad]) * pad)
    tail = bytes(byte ^ xor for byte in image[aes_size:])
    return b"\x07\x08V2\x08\x07" + struct.pack("<II", aes_size, len(tail)) + b"\x00" + encrypted + tail


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _jpeg(image_data: bytes = b"scan-data") -> bytes:
    return b"\xff\xd8\xff\xda\x00\x02" + image_data + b"\xff\xd9"


def test_decode_v2_verifies_jpeg_and_recovers_xor():
    key = b"0123456789abcdef"
    image = _jpeg()
    decoded = decode_v2(_v2_blob(image, key, 0xA7), key)
    assert decoded.data == image
    assert decoded.extension == ".jpg"
    assert decoded.xor_key == 0xA7
    assert image_extension(decoded.data) == ".jpg"


def test_decode_v2_trims_cache_bytes_after_jpeg():
    key = b"0123456789abcdef"
    image = _jpeg()
    blob = _v2_blob(image + b"wechat-cache-tail", key, 0xA7)
    decoded = decode_v2(blob, key)
    assert decoded.data == image


def test_decode_v2_skips_jpeg_eoi_bytes_inside_app_segment():
    key = b"0123456789abcdef"
    app_payload = b"metadata\xff\xd9inside"
    jpeg = (
        b"\xff\xd8\xff\xe0"
        + struct.pack(">H", len(app_payload) + 2)
        + app_payload
        + b"\xff\xda\x00\x02scan-data\xff\xd9"
    )
    decoded = decode_v2(_v2_blob(jpeg + b"opaque-cache-tail", key, 0xA7), key)
    assert decoded.data == jpeg


def test_decode_v2_trims_png_cache_tail_at_iend_chunk():
    key = b"0123456789abcdef"
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += _png_chunk(b"tEXt", b"IEND\xaeB`\x82")
    png += _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    png += _png_chunk(b"IEND", b"")
    decoded = decode_v2(_v2_blob(png + b"opaque-cache-tail", key, 0xA7), key)
    assert decoded.data == png


def test_decode_v2_trims_gif_cache_tail_after_trailer():
    key = b"0123456789abcdef"
    # A valid 1x1 GIF with 0x3B inside a comment sub-block before the trailer.
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    gif = gif[:19] + b"\x21\xfe\x01\x3b\x00" + gif[19:]
    decoded = decode_v2(_v2_blob(gif + b"opaque-cache-tail", key, 0xA7), key)
    assert decoded.data == gif


def test_image_extension_rejects_non_webp_riff_and_malformed_webp_size():
    assert image_extension(b"RIFF\x04\x00\x00\x00WAVE") is None
    key = b"0123456789abcdef"
    invalid = b"RIFF\x00\x00\x00\x00WEBP"
    with pytest.raises(ImageDecodeError):
        decode_v2(_v2_blob(invalid, key, 0xA7), key)


def test_decode_v2_trims_structurally_valid_webp_cache_tail():
    key = b"0123456789abcdef"
    chunk = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 10
    webp_body = b"WEBP" + chunk
    webp = b"RIFF" + struct.pack("<I", len(webp_body)) + webp_body
    decoded = decode_v2(_v2_blob(webp + b"opaque-cache-tail", key, 0xA7), key)
    assert decoded.data == webp


def test_decode_v2_rejects_wrong_key():
    key = b"0123456789abcdef"
    with pytest.raises(ImageDecodeError):
        decode_v2(_v2_blob(b"\xff\xd8\xfffake-image\xff\xd9", key, 0xA7), b"fedcba9876543210")


def test_decode_cache_file_requires_key_for_v2(tmp_path):
    path = tmp_path / "image"
    path.write_bytes(_v2_blob(b"\xff\xd8\xfffake-image\xff\xd9", b"0123456789abcdef", 0xA7))
    with pytest.raises(ImageDecodeError, match="图片密钥"):
        decode_cache_file(path)


def test_discover_local_image_key_from_kvcomm(tmp_path, monkeypatch):
    code = 1234567
    account = tmp_path / "wxid_demo0000_1234"
    db_dir = account / "db_storage"
    key = hashlib.md5(f"{code}wxid_demo0000".encode("utf-8")).hexdigest()[:16].encode("ascii")
    image = b"\xff\xd8\xff\xe0fake-image\xff\xd9"
    moment_id = "moment-test-001"
    media_id = "media-test-001"
    own_cache_name = next(iter(own_sns_cache_names(moment_id, media_id)))
    cache_file = account / "cache" / "2026-01" / "Sns" / "Img" / "aa" / own_cache_name
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(_v2_blob(image, key, code & 0xFF))
    unrelated = cache_file.with_name("0" * 30)
    unrelated.write_bytes(_v2_blob(image, b"fedcba9876543210", code & 0xFF))

    kvcomm = tmp_path / "appdata" / "Tencent" / "xwechat" / "net" / "kvcomm"
    kvcomm.mkdir(parents=True)
    (kvcomm / f"key_{code}_1_1_600_input.statistic").write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    probed = []
    original_probe = __import__("wechat_cli.core.image_cache", fromlist=["_probe_v2_key"])._probe_v2_key
    def track_probe(path, candidate_key):
        probed.append(path.name)
        return original_probe(path, candidate_key)
    monkeypatch.setattr("wechat_cli.core.image_cache._probe_v2_key", track_probe)

    moments = [{"id": moment_id, "images": [{"id": media_id}]}]
    found_key, found_xor = discover_local_image_key(db_dir, moments)
    assert found_key == key
    assert found_xor == code & 0xFF
    assert set(probed) == {own_cache_name}


def test_own_sns_cache_names_match_known_vectors():
    assert own_sns_cache_names("moment-test-001", "media-test-001") == {
        "f4327a21ec3c02012cc8624b335882",
        "afa26dd51b7cd48a3caedd0caffb38",
    }


def test_iter_own_sns_images_yields_only_derived_own_post_names(tmp_path, monkeypatch):
    account = tmp_path / "wxid_demo0000_1234"
    db_dir = account / "db_storage"
    own_names = own_sns_cache_names("moment-test-001", "media-test-001")
    unrelated_name = "0" * 30
    names = own_names | {unrelated_name}
    for name in names:
        cache_file = account / "cache" / "2026-01" / "Sns" / "Img" / "aa" / name
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"cache")

    original_open = Path.open
    opened = []

    def track_open(path, *args, **kwargs):
        if path.name == unrelated_name:
            raise AssertionError("unrelated Moments cache was opened")
        opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    moments = [{"id": "moment-test-001", "images": [{"id": "media-test-001"}]}]
    found = {item.path.name for item in iter_own_sns_images(db_dir, moments)}
    assert found == own_names
    assert set(opened) == own_names
