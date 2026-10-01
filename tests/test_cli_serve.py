"""Smoke tests for the `serve` subcommand argument parsing."""

from typer.testing import CliRunner

from piighost_api.cli import app


runner = CliRunner()


def test_serve_requires_config_flag_or_env(monkeypatch):
    monkeypatch.delenv("PIIGHOST_CONFIG", raising=False)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "config" in result.output.lower() or "config" in result.stderr.lower()


def test_serve_rejects_module_variable_format(monkeypatch, tmp_path):
    monkeypatch.delenv("PIIGHOST_CONFIG", raising=False)
    # The new CLI takes a file path, not a module:variable string.
    result = runner.invoke(app, ["serve", "--config", "pipeline:pipeline"])
    assert result.exit_code == 1


def test_serve_takes_a_hub_reference(monkeypatch):
    """A hub: reference is passed through, not looked up as a file."""
    monkeypatch.delenv("PIIGHOST_CONFIG", raising=False)
    started: list[str] = []
    monkeypatch.setattr(
        "piighost_api.cli.uvicorn.run", lambda *args, **kwargs: started.append("ok")
    )
    result = runner.invoke(
        app, ["serve", "--config", "hub:piighost/support-en:286909f6"]
    )
    assert result.exit_code == 0
    assert started == ["ok"]
    import os

    assert os.environ["PIIGHOST_CONFIG"] == "hub:piighost/support-en:286909f6"
