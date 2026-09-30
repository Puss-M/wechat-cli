"""Generate monthly, yearly, and journey reports from a local archive."""

import click

from ..core.reports import ReportError, build_journey_report, build_monthly_report, build_yearly_report, render_report


@click.command("report")
@click.option("--archive-dir", required=True, type=click.Path(file_okay=False, path_type=str), help="规范化归档目录")
@click.option("--kind", type=click.Choice(["monthly", "yearly", "journey"]), required=True,
              help="报告类型")
@click.option("--period", default=None, help="monthly 使用 YYYY-MM；yearly 使用 YYYY")
@click.option("--format", "fmt", type=click.Choice(["markdown", "html"]), default="markdown", show_default=True)
@click.option("--output", "output_path", default=None, type=click.Path(dir_okay=False, path_type=str),
              help="输出文件；默认写入归档目录 reports/")
def report(archive_dir, kind, period, fmt, output_path):
    """从本地朋友圈归档生成月报、年报或回忆旅程。"""
    try:
        if kind == "monthly":
            if not period:
                raise ReportError("monthly 必须提供 --period YYYY-MM")
            result = build_monthly_report(archive_dir, period)
            default_name = f"{period}-monthly.{ 'html' if fmt == 'html' else 'md' }"
        elif kind == "yearly":
            if not period:
                raise ReportError("yearly 必须提供 --period YYYY")
            result = build_yearly_report(archive_dir, period)
            default_name = f"{period}-yearly.{ 'html' if fmt == 'html' else 'md' }"
        else:
            result = build_journey_report(archive_dir)
            default_name = f"journey.{ 'html' if fmt == 'html' else 'md' }"
        output = output_path or str(__import__("pathlib").Path(archive_dir) / "reports" / default_name)
        target = render_report(result, archive_dir, output, fmt)
    except (ReportError, OSError, UnicodeError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"报告已生成: {target}")
