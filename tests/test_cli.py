"""Tests for cli.py — CLI parsing and app factory."""

import os
from unittest.mock import MagicMock, patch

import pytest

from piighost_api.cli import _create_app, main


def test_no_command_exits() -> None:
    with patch("sys.argv", ["piighost-api"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_serve_sets_env_and_runs_uvicorn() -> None:
    with patch("sys.argv", ["piighost-api", "serve", "mymod:pipe", "--host", "0.0.0.0", "--port", "9000", "--log-level", "debug"]):
        with patch("piighost_api.cli.uvicorn") as mock_uvicorn:
            main()

            assert os.environ["PIIGHOST_PIPELINE"] == "mymod:pipe"
            mock_uvicorn.run.assert_called_once_with(
                "piighost_api.cli:_create_app",
                factory=True,
                host="0.0.0.0",
                port=9000,
                log_level="debug",
            )


def test_create_app_factory() -> None:
    with patch.dict(os.environ, {"PIIGHOST_PIPELINE": "test:pipeline"}):
        with patch("piighost_api.app.create_app") as mock_create:
            mock_create.return_value = MagicMock()
            result = _create_app()
            mock_create.assert_called_once_with("test:pipeline")
            assert result is mock_create.return_value
