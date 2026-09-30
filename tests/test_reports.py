import json

from wechat_cli.commands.report import report
from wechat_cli.core.archive import import_moments_json
from wechat_cli.core.reports import build_monthly_report, build_yearly_report, render_report
from click.testing import CliRunner


def _archive(tmp_path):
    source = tmp_path / "moments.json"
    records = [
        {"id": "a", "time": "2025-12-31T23:00:00+08:00", "text": "跨年", "images": [], "location": {"city": "上海"}, "capture_source": "local_sns_cache", "ownership_verification": "xml_author"},
        {"id": "b", "time": "2026-01-02T12:00:00+08:00", "text": "新年", "images": [], "location": {"city": "杭州"}, "capture_source": "local_sns_cache", "ownership_verification": "xml_author"},
        {"id": "unknown", "time": "3月4日", "text": "缺年份", "images": [], "capture_source": "wechat_ui", "ownership_verification": "user_confirmed_ui"},
    ]
    source.write_text(json.dumps({"moments": records}, ensure_ascii=False), encoding="utf-8")
    archive = tmp_path / "archive"
    import_moments_json(source, archive, batch_id="reports")
    return archive


def test_monthly_and_yearly_reports_exclude_unassigned_dates(tmp_path):
    archive = _archive(tmp_path)
    month = build_monthly_report(archive, "2026-01")
    year = build_yearly_report(archive, "2026")
    assert [item["moment_id"] for item in month["records"]] == ["b"]
    assert year["monthly"]["2026-01"] == 1
    assert year["metrics"]["unassigned_time"] == 0
    assert year["monthly"]["2026-02"] == 0
    assert year["total_records"] == 3


def test_report_render_and_cli_write_portable_markdown_and_html(tmp_path):
    archive = _archive(tmp_path)
    result = build_yearly_report(archive, "2026")
    md = render_report(result, archive, archive / "reports" / "year.md", "markdown")
    html = render_report(result, archive, archive / "reports" / "year.html", "html")
    assert "2026 年度报告" in md.read_text(encoding="utf-8")
    assert "2026 年度报告" in html.read_text(encoding="utf-8")
    cli_result = CliRunner().invoke(report, ["--archive-dir", str(archive), "--kind", "journey"])
    assert cli_result.exit_code == 0, cli_result.output
    assert (archive / "reports" / "journey.md").is_file()


def test_report_help_does_not_touch_archive():
    result = CliRunner().invoke(report, ["--help"])
    assert result.exit_code == 0
    assert "monthly" in result.output
