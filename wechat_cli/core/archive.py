"""Canonical local archive for the user's own Moments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "1"
_BATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ASSET_KEYS = {"local_path", "capture_screenshot", "screenshot"}


class ArchiveError(ValueError):
    """The input cannot be safely added to the canonical archive."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_time(record: dict) -> tuple[int | None, str | None, str, str | None]:
    raw_epoch = record.get("created_at_epoch")
    if raw_epoch is not None:
        try:
            epoch = int(raw_epoch)
            if epoch >= 10**12:
                epoch //= 1000
            return epoch, record.get("timezone") or "unknown", "second", None
        except (TypeError, ValueError):
            pass

    value = str(record.get("time") or "").strip()
    if not value:
        return None, None, "unknown", None
    chinese = re.fullmatch(
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        value,
    )
    if chinese:
        year, month, day, hour, minute, second = chinese.groups()
        precision = "second" if second else "minute" if hour else "day"
        local = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if hour:
            local += f"T{int(hour):02d}:{minute}:{second or '00'}"
        return None, None, precision, local
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        short_date = re.fullmatch(r"(\d{1,2})月(\d{1,2})日?", value)
        if short_date:
            return None, None, "unknown", value
        return None, None, "unknown", None
    if parsed.tzinfo is None:
        precision = "second" if parsed.second else "minute" if parsed.hour or parsed.minute else "day"
        return None, None, precision, value
    precision = "second" if parsed.second else "minute"
    return int(parsed.timestamp()), parsed.tzinfo.tzname(parsed) or "unknown", precision, None


def _identity(record: dict, batch_id: str, index: int) -> tuple[str, str]:
    source = str(record.get("capture_source") or "unknown")
    if source == "wechat_ui":
        return f"capture-{batch_id}-{index:06d}", "low"
    value = str(record.get("id") or "").strip()
    if value:
        return value, "high" if source == "local_sns_cache" else "medium"
    fallback = {
        "time": record.get("time"),
        "text": record.get("text"),
        "link": record.get("link"),
        "images": record.get("images"),
    }
    return f"capture-{_sha256_bytes(_json_bytes(fallback))[:24]}", "low"


def _copy_assets(value, archive_root: Path, asset_records: list[dict], missing: list[dict],
                 staged_assets: dict[str, Path], key: str | None = None):
    if isinstance(value, dict):
        return {
            item_key: _copy_assets(item_value, archive_root, asset_records, missing, staged_assets, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_copy_assets(item, archive_root, asset_records, missing, staged_assets, key) for item in value]
    if key not in _ASSET_KEYS or not value:
        return value

    source = Path(str(value)).expanduser()
    if not source.is_file():
        missing.append({"field": key, "source_name": source.name, "status": "missing"})
        return None
    try:
        digest = _sha256_file(source)
        suffix = source.suffix.lower() or ".bin"
        target_name = f"{digest}{suffix}"
        target = archive_root / "assets" / target_name
        staged = staged_assets.get(target_name)
        if staged is None:
            staging_dir = archive_root / ".staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            staged = staging_dir / target_name
            if not target.exists():
                shutil.copyfile(source, staged)
            else:
                staged = target
            staged_assets[target_name] = staged
        if not target.exists() and staged != target:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and staged == target:
            pass
        elif not target.exists():
            pass
        relative = str(Path("assets") / target_name).replace("\\", "/")
        asset_records.append({
            "asset_id": digest,
            "relative_path": relative,
            "sha256": digest,
            "size": source.stat().st_size,
            "extension": suffix.lstrip("."),
            "kind": key,
            "source_path": str(source),
            "status": "available",
        })
        return relative
    except OSError as exc:
        missing.append({"field": key, "source_name": source.name, "status": "error", "error": str(exc)})
        return None


def _normalise_record(record: dict, archive_root: Path, captured_at: str, batch_id: str, index: int,
                      staged_assets: dict[str, Path]):
    if not isinstance(record, dict):
        raise ArchiveError("朋友圈记录必须是对象")
    moment_id, identity_confidence = _identity(record, batch_id, index)
    copied_assets: list[dict] = []
    missing_assets: list[dict] = []
    normalised = _copy_assets(record, archive_root, copied_assets, missing_assets, staged_assets)
    epoch, record_timezone, precision, parsed_local = _parse_time(normalised)
    source = str(normalised.get("capture_source") or "unknown")
    verification = str(normalised.get("ownership_verification") or "unknown")
    status = "complete"
    if (epoch is None or precision == "unknown" or verification != "xml_author" or missing_assets):
        status = "incomplete"
    normalised["archive_assets"] = [
        {key: item[key] for key in ("asset_id", "relative_path", "kind", "status")}
        for item in copied_assets
    ]
    if missing_assets:
        normalised["archive_missing_assets"] = missing_assets
    content_hash = _sha256_bytes(_json_bytes(normalised))
    return {
        "moment_id": moment_id,
        "created_at_epoch": epoch,
        "created_at_local": parsed_local or normalised.get("time"),
        "timezone": normalised.get("timezone") or record_timezone,
        "time_precision": precision,
        "text": str(normalised.get("text") or ""),
        "source": source,
        "verification": verification,
        "captured_at": captured_at,
        "content_hash": content_hash,
        "status": status,
        "identity_confidence": identity_confidence,
        "record_json": json.dumps(normalised, ensure_ascii=False, sort_keys=True),
        "assets": copied_assets,
    }


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            batch_id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            snapshot_path TEXT NOT NULL,
            record_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS moments (
            moment_id TEXT PRIMARY KEY,
            created_at_epoch INTEGER,
            created_at_local TEXT,
            timezone TEXT,
            time_precision TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT NOT NULL,
            verification TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            capture_batch_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            identity_confidence TEXT NOT NULL,
            record_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS moment_versions (
            moment_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            record_json TEXT NOT NULL,
            PRIMARY KEY (moment_id, content_hash)
        );
        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            extension TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS asset_links (
            asset_id TEXT NOT NULL,
            moment_id TEXT NOT NULL,
            field TEXT NOT NULL,
            PRIMARY KEY (asset_id, moment_id, field)
        );
        """
    )
    existing = conn.execute("SELECT value FROM archive_meta WHERE key = 'schema_version'").fetchone()
    if existing is not None and int(existing[0]) > int(SCHEMA_VERSION):
        raise ArchiveError(f"归档 schema 版本过高: {existing[0]} > {SCHEMA_VERSION}")
    conn.execute(
        "INSERT OR IGNORE INTO archive_meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )


def import_moments_json(input_path, archive_dir, batch_id: str | None = None) -> dict:
    """Import one moments JSON export into a portable local archive."""
    source = Path(input_path).expanduser().resolve()
    root = Path(archive_dir).expanduser().resolve()
    if not source.is_file():
        raise ArchiveError(f"找不到输入文件: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"无法读取 JSON 输入: {exc}") from exc
    records = payload.get("moments") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ArchiveError("输入 JSON 缺少 moments 数组")
    if batch_id is None:
        batch_id = f"batch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_sha256_file(source)[:8]}"
    if not _BATCH_RE.fullmatch(batch_id):
        raise ArchiveError("batch_id 只能包含字母、数字、点、下划线和短横线")

    imported_at = _now_utc()
    source_hash = _sha256_file(source)
    root.mkdir(parents=True, exist_ok=True)
    snapshot_dir = root / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{batch_id}.json"
    if snapshot_path.exists():
        raise ArchiveError(f"采集批次已存在，未覆盖: {snapshot_path}")
    db_path = root / "moments.sqlite"
    with sqlite3.connect(db_path) as conn:
        _init_db(conn)
        if conn.execute("SELECT 1 FROM snapshots WHERE batch_id = ?", (batch_id,)).fetchone():
            raise ArchiveError(f"SQLite 中已存在采集批次，未覆盖: {batch_id}")
    staging_root = Path(tempfile.mkdtemp(prefix=f"{batch_id}-", dir=root / ".staging" if (root / ".staging").exists() else root))
    staged_assets: dict[str, Path] = {}
    try:
        normalised_records = [
            _normalise_record(record, root, imported_at, batch_id, index, staged_assets)
            for index, record in enumerate(records, start=1)
        ]
    except Exception:
        for staged in staged_assets.values():
            if staged.parent.name == ".staging":
                staged.unlink(missing_ok=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    snapshot_payload = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "imported_at": imported_at,
        "source": str(source),
        "source_sha256": source_hash,
        "payload": payload,
    }
    temporary_snapshot = staging_root / snapshot_path.name
    temporary_snapshot.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_path = root / "manifests" / f"{batch_id}.json"
    temporary_manifest = staging_root / f"{batch_id}.manifest.json"
    temporary_manifest.write_text(
        json.dumps({
            "schema_version": SCHEMA_VERSION,
            "batch_id": batch_id,
            "assets": [asset for item in normalised_records for asset in item["assets"]],
            "missing": [missing for item in normalised_records
                        for missing in json.loads(item["record_json"]).get("archive_missing_assets", [])],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    new_count = updated_count = duplicate_count = incomplete_count = 0
    assets_available = assets_missing = 0
    promoted: list[Path] = []
    try:
      with sqlite3.connect(db_path) as conn:
        _init_db(conn)
        conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, imported_at, str(source), source_hash,
             str(snapshot_path.relative_to(root)).replace("\\", "/"), len(records)),
        )
        for item in normalised_records:
            if item["status"] == "incomplete":
                incomplete_count += 1
            for asset in item["assets"]:
                conn.execute(
                    "INSERT OR IGNORE INTO assets VALUES (?, ?, ?, ?, ?, ?)",
                    (asset["asset_id"], asset["relative_path"], asset["sha256"], asset["size"], asset["extension"], asset["status"]),
                )
                assets_available += 1
                conn.execute(
                    "INSERT OR IGNORE INTO asset_links VALUES (?, ?, ?)",
                    (asset["asset_id"], item["moment_id"], asset["kind"]),
                )
            assets_missing += len(json.loads(item["record_json"]).get("archive_missing_assets", []))
            existing = conn.execute(
                "SELECT content_hash, version, verification FROM moments WHERE moment_id = ?",
                (item["moment_id"],),
            ).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO moment_versions VALUES (?, ?, ?, ?, ?)",
                (item["moment_id"], item["content_hash"], batch_id, imported_at, item["record_json"]),
            )
            if existing is None:
                conn.execute(
                    """INSERT INTO moments(
                        moment_id, created_at_epoch, created_at_local, timezone, time_precision,
                        text, source, verification, captured_at, capture_batch_id, content_hash,
                        status, identity_confidence, record_json, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["moment_id"], item["created_at_epoch"], item["created_at_local"], item["timezone"],
                     item["time_precision"], item["text"], item["source"], item["verification"],
                     item["captured_at"], batch_id, item["content_hash"], item["status"],
                     item["identity_confidence"], item["record_json"], imported_at, imported_at),
                )
                new_count += 1
            elif existing[0] == item["content_hash"]:
                conn.execute("UPDATE moments SET last_seen_at = ? WHERE moment_id = ?", (imported_at, item["moment_id"]))
                duplicate_count += 1
            else:
                rank = {"unknown": 0, "user_confirmed_ui": 1, "xml_author": 2}
                if rank.get(item["verification"], 0) >= rank.get(existing[2], 0):
                    conn.execute(
                        """UPDATE moments SET created_at_epoch=?, created_at_local=?, timezone=?, time_precision=?,
                        text=?, source=?, verification=?, captured_at=?, capture_batch_id=?, content_hash=?,
                        status=?, identity_confidence=?, record_json=?, last_seen_at=?, version=version+1
                        WHERE moment_id=?""",
                        (item["created_at_epoch"], item["created_at_local"], item["timezone"], item["time_precision"],
                         item["text"], item["source"], item["verification"], item["captured_at"], batch_id,
                         item["content_hash"], item["status"], item["identity_confidence"], item["record_json"],
                         imported_at, item["moment_id"]),
                    )
                    updated_count += 1
                else:
                    duplicate_count += 1
        for target_name, staged in staged_assets.items():
            target = root / "assets" / target_name
            if not target.exists() and staged != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                promoted.append(target)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary_snapshot, snapshot_path)
        promoted.append(snapshot_path)
        os.replace(temporary_manifest, manifest_path)
        promoted.append(manifest_path)
        conn.commit()
    except (OSError, sqlite3.Error):
        for path in promoted:
            path.unlink(missing_ok=True)
        for staged in staged_assets.values():
            if staged.parent.name == ".staging":
                staged.unlink(missing_ok=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_dir = root / ".staging"
    if staging_dir.is_dir() and not any(staging_dir.iterdir()):
        staging_dir.rmdir()
    return {
        "batch_id": batch_id,
        "records": len(records),
        "new": new_count,
        "updated": updated_count,
        "duplicate": duplicate_count,
        "incomplete": incomplete_count,
        "assets_available": assets_available,
        "assets_missing": assets_missing,
        "archive": str(root),
    }
