"""Tests for the /v1/labels route."""

from pathlib import Path

from litestar.testing import TestClient

from piighost_api.app import create_app


FIXTURES = Path(__file__).parent / "fixtures"


def test_labels_returns_grouped_detector_labels():
    app = create_app(FIXTURES / "multi_detector.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/labels")
    assert response.status_code == 200
    body = response.json()
    assert "pipeline" in body
    assert body["pipeline"]["schema_version"] == 1
    assert "detectors" in body
    assert len(body["detectors"]) == 2
    names = [d["name"] for d in body["detectors"]]
    assert "common" in names
    assert "secondary" in names


def test_v1_config_route_is_removed():
    app = create_app(FIXTURES / "minimal.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/config")
    assert response.status_code == 404
