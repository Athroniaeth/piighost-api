"""Tests for the OpenTelemetry observation setup."""

import pytest

from piighost_api.observation import configure_observation, otlp_endpoint

_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def _clear_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENDPOINT_VARS:
        monkeypatch.delenv(var, raising=False)


def test_otlp_endpoint_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_endpoints(monkeypatch)
    assert otlp_endpoint() is None


def test_otlp_endpoint_reads_the_generic_var(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_endpoints(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert otlp_endpoint() == "http://collector:4318"


def test_otlp_endpoint_prefers_the_traces_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://generic:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://traces:4318")
    assert otlp_endpoint() == "http://traces:4318"


def test_configure_observation_is_a_noop_without_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_endpoints(monkeypatch)
    assert configure_observation() is False
