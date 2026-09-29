"""Capture own visible Moments from the WeChat desktop UI."""

import json
import os
import tempfile
from pathlib import Path

import click

from ..core.moments import render_markdown
from ..core.ui_moments import UiCaptureError, collect_visible_moments, merge_moment_records


@click.command("moments-ui")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=str),
              help="保存 JSON 或 Markdown 的路径")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "markdown"]))
@click.option("--confirm-own", is_flag=True,
              help="确认当前微信窗口已打开‘我 -> 朋友圈’")
@click.option("--resume", is_flag=True, help="读取已有 JSON 并合并，原子替换输出")
@click.option("--max-pages", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("--pause", type=click.FloatRange(min=0), default=1.2, show_default=True)
@click.option("--window-title", default="微信|WeChat|Weixin", show_default=True)
def moments_ui(output, fmt, confirm_own, resume, max_pages, pause, window_title):
    """从已打开的微信‘我 -> 朋友圈’页面采集本地截图和可见文字。"""
    if not confirm_own:
        raise click.ClickException("必须传入 --confirm-own，确认当前页面是‘我 -> 朋友圈’")
    if os.path.exists(output) and not resume:
        raise click.ClickException(f"输出文件已存在，未覆盖: {output}")
    try:
        previous = []
        if resume:
            payload = json.loads(Path(output).read_text(encoding="utf-8"))
            previous = payload.get("moments", [])
        root = Path(output).resolve().parent / "moments-ui"
        records, stats = collect_visible_moments(
            root, max_pages=max_pages, pause=pause, title_pattern=window_title
        )
        records = merge_moment_records(previous, records)
        diagnostics = {
            "ui_capture": stats,
            "resumed_from": str(Path(output).resolve()) if resume else None,
            "exported_count": len(records),
        }
        if fmt == "markdown":
            content = render_markdown(records, 0, diagnostics)
        else:
            content = json.dumps({
                "source": "wechat_desktop_ui",
                "scope": "visible_ui_only",
                "count": len(records),
                "diagnostics": diagnostics,
                "moments": records,
            }, ensure_ascii=False, indent=2) + "\n"
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=output_path.parent,
            prefix=f".{output_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    except (UiCaptureError, OSError, json.JSONDecodeError, KeyError) as exc:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise click.ClickException(f"微信界面采集失败，未覆盖已有结果: {exc}") from exc
    click.echo(
        f"微信界面采集完成：扫描 {stats['pages']} 页，记录 {stats['records']} 条；输出: {output_path}",
        err=True,
    )
