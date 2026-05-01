"""Tests for cli.py — Typer-based multi-subcommand entrypoint."""

import os
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from piighost_api.cli import _create_app, app


runner = CliRunner()


def test_no_command_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, [])
    # Typer with no_args_is_help=True prints the help banner.
    # Exit code is 0 or 2 depending on the Typer/Click version.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.stdout


def test_serve_sets_env_and_runs_uvicorn() -> None:
    with patch("piighost_api.cli.uvicorn") as mock_uvicorn:
        result = runner.invoke(
            app,
            [
                "serve",
                "mymod:pipe",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--log-level",
                "debug",
            ],
        )

    assert result.exit_code == 0
    assert os.environ["PIIGHOST_PIPELINE"] == "mymod:pipe"
    mock_uvicorn.run.assert_called_once_with(
        "piighost_api.cli:_create_app",
        factory=True,
        host="0.0.0.0",
        port=9000,
        log_level="debug",
    )


def test_dataset_extract_help() -> None:
    result = runner.invoke(app, ["dataset", "extract", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.stdout
    assert "--since" in result.stdout
    assert "--mode" in result.stdout


def test_dataset_metrics_help() -> None:
    result = runner.invoke(app, ["dataset", "metrics", "--help"])
    assert result.exit_code == 0
    assert "--input" in result.stdout
    assert "--match-mode" in result.stdout
    assert "--source" in result.stdout


def test_dataset_extract_missing_credentials_exits_one(tmp_path, monkeypatch) -> None:
    # chdir into a temp dir so the CLI's load_dotenv() does not pick up
    # the repo's real .env (which would defeat the missing-creds assertion).
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(
            app, ["dataset", "extract", "--output", "/tmp/should-not-exist.jsonl"]
        )
    assert result.exit_code == 1
    assert (
        "LANGFUSE_PUBLIC_KEY" in result.stderr or "LANGFUSE_PUBLIC_KEY" in result.stdout
    )


def test_create_app_factory() -> None:
    with patch.dict(os.environ, {"PIIGHOST_PIPELINE": "test:pipeline"}):
        with patch("piighost_api.app.create_app") as mock_create:
            mock_create.return_value = MagicMock()
            result = _create_app()
            mock_create.assert_called_once_with("test:pipeline")
            assert result is mock_create.return_value
