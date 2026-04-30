"""Tests for the observation backend resolution layer."""

from __future__ import annotations

import importlib.util
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from piighost_api.observation import (
    MultipleObservationBackendsError,
    ObservationBackend,
    create_observation_service,
    detect_observation_backend,
    load_observation_service,
)


@pytest.fixture(autouse=True)
def _clean_obs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every observation env var so each test starts from a known state."""
    for var in ("LANGFUSE_PUBLIC_KEY", "OPIK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ----------------------------------------------------------------------
# detect_observation_backend
# ----------------------------------------------------------------------


def test_detect_returns_none_when_no_env_set() -> None:
    assert detect_observation_backend() is ObservationBackend.NONE


def test_detect_returns_langfuse_when_only_langfuse_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    assert detect_observation_backend() is ObservationBackend.LANGFUSE


def test_detect_returns_opik_when_only_opik_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIK_API_KEY", "opik-key")
    assert detect_observation_backend() is ObservationBackend.OPIK


def test_detect_raises_when_multiple_backends_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("OPIK_API_KEY", "opik-key")
    with pytest.raises(MultipleObservationBackendsError) as excinfo:
        detect_observation_backend()
    assert "langfuse" in str(excinfo.value)
    assert "opik" in str(excinfo.value)


def test_detect_ignores_empty_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    assert detect_observation_backend() is ObservationBackend.NONE


# ----------------------------------------------------------------------
# create_observation_service
# ----------------------------------------------------------------------


def test_create_returns_none_for_none_backend() -> None:
    assert create_observation_service(ObservationBackend.NONE) is None


def test_create_phoenix_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        create_observation_service(ObservationBackend.PHOENIX)


@pytest.mark.skipif(
    importlib.util.find_spec("langfuse") is None,
    reason="langfuse SDK not installed",
)
def test_create_langfuse_returns_langfuse_service() -> None:
    fake_client = MagicMock()
    with patch("langfuse.Langfuse", return_value=fake_client):
        service = create_observation_service(ObservationBackend.LANGFUSE)

    from piighost.observation.langfuse import LangfuseObservationService

    assert isinstance(service, LangfuseObservationService)


@pytest.mark.skipif(
    importlib.util.find_spec("opik") is None,
    reason="opik SDK not installed",
)
def test_create_opik_returns_opik_service() -> None:
    fake_client = MagicMock()
    with patch("opik.Opik", return_value=fake_client), patch("opik.set_global_client"):
        service = create_observation_service(ObservationBackend.OPIK)

    from piighost.observation.opik import OpikObservationService

    assert isinstance(service, OpikObservationService)


def test_create_langfuse_raises_import_error_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When langfuse is not installed, the piighost adapter raises ImportError."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langfuse":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.delitem(sys.modules, "piighost.observation.langfuse", raising=False)

    with pytest.raises(ImportError, match="piighost\\[langfuse\\]"):
        create_observation_service(ObservationBackend.LANGFUSE)


# ----------------------------------------------------------------------
# load_observation_service (composition)
# ----------------------------------------------------------------------


def test_load_returns_none_when_no_env_set() -> None:
    assert load_observation_service() is None


def test_load_propagates_multiple_backends_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("OPIK_API_KEY", "opik-key")
    with pytest.raises(MultipleObservationBackendsError):
        load_observation_service()
