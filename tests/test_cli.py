from click.testing import CliRunner

from wechat_cli import main


def test_moments_help_does_not_load_wechat_context(monkeypatch):
    def fail_if_initialized(_config_path):
        raise AssertionError("help should not initialize the WeChat data context")

    monkeypatch.setattr(main, "AppContext", fail_if_initialized)
    result = CliRunner().invoke(main.cli, ["moments", "--help"])

    assert result.exit_code == 0, result.output
    assert "--decode-images" in result.output
    assert "--ui-collect" in result.output


def test_moments_ui_requires_explicit_own_confirmation():
    result = CliRunner().invoke(main.cli, ["moments-ui", "--output", "out.json"])
    assert result.exit_code != 0
    assert "--confirm-own" in result.output
