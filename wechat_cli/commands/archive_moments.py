"""Import a Moments JSON export into the canonical local archive."""

import json
import sqlite3

import click

from ..core.archive import ArchiveError, import_moments_json


@click.command("archive-moments")
@click.option("--input", "input_path", required=True, type=click.Path(dir_okay=False, path_type=str),
              help="moments 命令生成的 JSON 文件")
@click.option("--archive-dir", required=True, type=click.Path(file_okay=False, path_type=str),
              help="规范化归档目录")
@click.option("--batch-id", default=None, help="可选采集批次 ID")
def archive_moments(input_path, archive_dir, batch_id):
    """把自己的朋友圈 JSON 导入可重跑的本地归档。"""
    try:
        stats = import_moments_json(input_path, archive_dir, batch_id=batch_id)
    except (ArchiveError, OSError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(stats, ensure_ascii=False, indent=2))
