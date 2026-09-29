"""moments command: export the current account's cached Moments text."""

import json
import os
import sqlite3
from pathlib import Path

import click

from ..core.contacts import get_self_username
from ..core.config import load_config
from ..core.image_cache import (
    ImageDecodeError,
    decode_cache_file,
    iter_own_sns_images,
    output_name,
    discover_local_image_key,
    read_image_key,
)
from ..core.moments import (
    MomentDataError,
    download_own_moment_images,
    load_own_moments,
    render_markdown,
)


@click.command("moments")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "markdown"]))
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=str),
              help="保存路径；不指定则输出到终端")
@click.option("--include-empty", is_flag=True, help="同时保留文字、图片、地点和链接均为空的记录")
@click.option("--decode-images", is_flag=True, help="从本机 Sns/Img 缓存解码图片到新目录；不访问网络")
@click.option("--download-images", is_flag=True,
              help="只下载当前账号自己朋友圈 XML 中的图片到本地；不读取混杂缓存")
@click.option("--image-key-file", type=click.Path(dir_okay=False, path_type=str),
              help="V2 图片密钥文件：16 字节 ASCII 或 32 位十六进制")
@click.option("--auto-image-key", is_flag=True,
              help="从当前账号自己的图片缓存派生密钥；必要时回退到可选 wx_key；不联网")
@click.option("--xor-key", type=str, help="可选的图片尾部 XOR 密钥，例如 0xF4")
@click.option("--image-output", type=click.Path(file_okay=False, path_type=str),
              help="解码图片输出目录；默认 ~/.wechat-cli/decoded_images")
@click.option("--image-limit", type=click.IntRange(min=0), default=0, show_default=True,
              help="最多解码多少个缓存文件；0 表示全部")
@click.option("--download-limit", type=click.IntRange(min=0), default=0, show_default=True,
              help="最多下载多少张自己的朋友圈图片；0 表示全部")
@click.pass_context
def moments(ctx, fmt, output_path, include_empty, decode_images, download_images,
            image_key_file, auto_image_key, xor_key, image_output, image_limit,
            download_limit):
    """导出当前账号在本机缓存中的朋友圈内容和元数据。"""
    app = ctx.obj
    self_username = get_self_username(app.db_dir, app.cache, app.decrypted_dir)
    if not self_username:
        raise click.ClickException("无法确认当前微信账号 ID，已停止导出")

    db_path = app.cache.get(os.path.join("sns", "sns.db"))
    if not db_path:
        raise click.ClickException("无法访问 sns/sns.db；请检查本机缓存和 wechat-cli 初始化状态")

    try:
        records, skipped_empty = load_own_moments(db_path, self_username, include_empty)
    except (MomentDataError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_path and os.path.exists(output_path):
        raise click.ClickException(f"输出文件已存在，未覆盖: {output_path}")

    download_stats = None
    if download_images:
        try:
            cfg = getattr(app, "cfg", None) or load_config()
            destination = Path(image_output or cfg["decoded_image_dir"]).resolve()
            download_stats = download_own_moment_images(
                records, destination, limit=download_limit
            )
        except (OSError, KeyError) as exc:
            raise click.ClickException(f"无法准备自己的朋友圈图片下载: {exc}") from exc

    if fmt == "markdown":
        content = render_markdown(records, skipped_empty)
    else:
        content = json.dumps({
            "source": "local_sns_cache",
            "scope": "cached_only",
            "count": len(records),
            "skipped_empty": skipped_empty,
            "moments": records,
        }, ensure_ascii=False, indent=2) + "\n"

    if output_path:
        try:
            with open(output_path, "x", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            raise click.ClickException(f"无法写入输出文件: {exc}") from exc
        click.echo(f"已导出 {len(records)} 条到: {output_path}（跳过无内容 {skipped_empty} 条）", err=True)
    else:
        click.echo(content, nl=False)

    if download_stats:
        click.echo(
            "自己的朋友圈图片下载完成："
            f"新增 {download_stats['downloaded']}，复用 {download_stats['reused']}，"
            f"失败 {download_stats['failed']}；目录: "
            f"{Path(image_output or (getattr(app, 'cfg', None) or load_config())['decoded_image_dir']).resolve()}",
            err=True,
        )

    if decode_images:
        _decode_sns_images(app, records, image_key_file, auto_image_key, xor_key, image_output, image_limit)


def _parse_xor_key(value):
    if value is None:
        return None
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise click.ClickException("--xor-key 应为 0-255 的整数或 0x 十六进制值") from exc
    if not 0 <= parsed <= 255:
        raise click.ClickException("--xor-key 必须在 0 到 255 之间")
    return parsed


def _decode_sns_images(app, records, image_key_file, auto_image_key, xor_key, image_output, image_limit):
    try:
        if image_key_file and auto_image_key:
            raise ImageDecodeError("--image-key-file 与 --auto-image-key 只能二选一")
        auto_xor = None
        if image_key_file:
            key = read_image_key(image_key_file)
        elif auto_image_key:
            key, auto_xor = discover_local_image_key(app.db_dir, records)
        else:
            key = None
        parsed_xor = _parse_xor_key(xor_key)
        if parsed_xor is None:
            parsed_xor = auto_xor
        cfg = getattr(app, "cfg", None) or load_config()
        destination = Path(image_output or cfg["decoded_image_dir"]).resolve()
        destination.mkdir(parents=True, exist_ok=True)
    except (OSError, ImageDecodeError) as exc:
        raise click.ClickException(f"无法准备图片解码: {exc}") from exc

    manifest = []
    scanned = decoded = failed = 0
    for item in iter_own_sns_images(app.db_dir, records):
        if image_limit and scanned >= image_limit:
            break
        scanned += 1
        try:
            result = decode_cache_file(item.path, key, parsed_xor)
            name = output_name(item.path, result.data, result.extension)
            target = destination / name
            if not target.exists():
                target.write_bytes(result.data)
            decoded += 1
            manifest.append({
                "source": str(item.path.relative_to(Path(app.db_dir).resolve().parent)),
                "output": str(target),
                "month": item.month,
                "format": result.extension.lstrip("."),
                "size": len(result.data),
                "xor_key": f"0x{result.xor_key:02X}",
                "version": result.version,
            })
        except (OSError, ImageDecodeError) as exc:
            failed += 1
            manifest.append({
                "source": str(item.path.relative_to(Path(app.db_dir).resolve().parent)),
                "month": item.month,
                "status": "failed",
                "error": str(exc),
            })

    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        raise click.ClickException(f"图片 manifest 已存在，未覆盖: {manifest_path}")
    manifest_path.write_text(json.dumps({
        "source": "local_sns_image_cache",
        "scope": "own_moments_only",
        "scanned": scanned,
        "decoded": decoded,
        "failed": failed,
        "images": manifest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    click.echo(
        f"自己的朋友圈图片缓存扫描完成：扫描 {scanned}，成功 {decoded}，失败 {failed}；"
        f"manifest: {manifest_path}",
        err=True,
    )
