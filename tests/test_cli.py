"""Tests for cli.py — Typer-based multi-subcommand entrypoint."""

import os
import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from piighost_api.cli import _create_app, app


runner = CliRunner()


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _plain(text: str) -> str:
    """Strip ANSI escape sequences and collapse whitespace.

    Typer renders help output via Rich, which emits bold sequences
    (and may wrap option names across lines on narrow CI terminals).
    Tests need to assert that an option name appears, regardless of
    styling or wrapping.
    """
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", text))


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
    out = _plain(result.stdout)
    assert "--output" in out
    assert "--since" in out
    assert "--mode" in out


def test_dataset_metrics_help() -> None:
    result = runner.invoke(app, ["dataset", "metrics", "--help"])
    assert result.exit_code == 0
    out = _plain(result.stdout)
    assert "--input" in out
    assert "--match-mode" in out
    assert "--source" in out


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
