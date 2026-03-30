"""Tests for loader.py — dynamic pipeline loading."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from piighost.pipeline.thread import ThreadAnonymizationPipeline

from piighost_api.loader import load_pipeline


def test_missing_colon() -> None:
    with pytest.raises(ValueError, match="module:variable"):
        load_pipeline("no_colon_here")


def test_invalid_module() -> None:
    with pytest.raises(ImportError):
        load_pipeline("nonexistent_module_xyz:pipeline")


def test_invalid_variable() -> None:
    with pytest.raises(AttributeError):
        load_pipeline("os:nonexistent_variable_xyz")


def test_wrong_type() -> None:
    with pytest.raises(TypeError, match="ThreadAnonymizationPipeline"):
        load_pipeline("os:path")


def test_load_valid_pipeline() -> None:
    mock_pipeline = MagicMock(spec=ThreadAnonymizationPipeline)
    fake_module = types.ModuleType("fake_pipeline_mod")
    fake_module.pipeline = mock_pipeline

    with patch.dict(sys.modules, {"fake_pipeline_mod": fake_module}):
        result = load_pipeline("fake_pipeline_mod:pipeline")
        assert result is mock_pipeline


def test_adds_cwd_to_syspath(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cwd = str(tmp_path)
    if cwd in sys.path:
        sys.path.remove(cwd)

    mock_pipeline = MagicMock(spec=ThreadAnonymizationPipeline)
    fake_module = types.ModuleType("test_mod")
    fake_module.pipe = mock_pipeline

    with patch.dict(sys.modules, {"test_mod": fake_module}):
        load_pipeline("test_mod:pipe")

    assert cwd in sys.path
