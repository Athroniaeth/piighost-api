"""Tests for the /v1/labels route."""

from pathlib import Path

from litestar.testing import TestClient

from piighost_api.app import create_app


FIXTURES = Path(__file__).parent / "fixtures"


def test_labels_reports_name_and_detector(monkeypatch) -> None:
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    app = create_app(FIXTURES / "multi_detector.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/labels")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo"
    assert body["detector"] == "composite"
    # The vocabulary unions the composite's two regex detectors (EMAIL, IP_V4).
    assert body["labels"] == ["EMAIL", "IP_V4"]


def test_v1_config_route_is_removed(monkeypatch) -> None:
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    app = create_app(FIXTURES / "minimal.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/config")
    assert response.status_code == 404
