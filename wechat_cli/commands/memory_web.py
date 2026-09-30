"""Build a local editorial memory web app from a Moments archive."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import click


def _read_records(archive_dir: Path) -> list[dict]:
    db_path = archive_dir / "moments.sqlite"
    if not db_path.is_file():
        raise click.ClickException(f"找不到归档数据库: {db_path}")
    records = []
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT moment_id, created_at_epoch, created_at_local, timezone, text, "
                "source, verification, status, record_json FROM moments"
            ).fetchall()
    except sqlite3.Error as exc:
        raise click.ClickException(f"无法读取归档数据库: {exc}") from exc
    for row in rows:
        try:
            payload = json.loads(row[8])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload.update({
            "moment_id": row[0], "created_at_epoch": row[1], "created_at_local": row[2],
            "timezone": row[3], "text": row[4], "source": row[5],
            "verification": row[6], "status": row[7],
        })
        records.append(payload)
    return records


@click.command("memory-web")
@click.option("--archive-dir", required=True, type=click.Path(file_okay=False, path_type=str),
              help="规范化朋友圈归档目录")
@click.option("--output-dir", default=None, type=click.Path(file_okay=False, path_type=str),
              help="输出 Web App 目录；默认写入归档目录 memory-app/")
def memory_web(archive_dir, output_dir):
    """从本地朋友圈归档生成私人记忆 Web App。"""
    archive = Path(archive_dir).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve() if output_dir else archive / "memory-app"
    source_dir = Path(__file__).resolve().parents[2] / "web" / "memory-app"
    try:
        records = _read_records(archive)
        target.mkdir(parents=True, exist_ok=True)
        for name in ("index.html", "styles.css", "editorial.css", "app.js",
                     "annual-report.html", "annual-report.css", "annual-image.css", "annual-report.js"):
            shutil.copyfile(source_dir / name, target / name)
        (target / "data.js").write_text(
            "window.MEMORY_DATA = " + json.dumps({"records": records}, ensure_ascii=False) + ";\n",
            encoding="utf-8", newline="\n",
        )
        click.echo(f"记忆 Web App 已生成: {target / 'index.html'}")
        click.echo("可在该目录启动本地静态服务器后打开，不会上传朋友圈数据。")
    except OSError as exc:
        raise click.ClickException(f"无法生成 Web App: {exc}") from exc
