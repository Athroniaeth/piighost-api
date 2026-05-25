"""Smoke tests for the `serve` subcommand argument parsing."""

from pathlib import Path

import pytest
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
