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


def test_the_labels_of_a_hub_catalog_come_from_the_hub(monkeypatch) -> None:
    """A catalog is a hub reference in piighost 2.0, so its labels are pulled."""
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    group = {"EMAIL": r"\S+@\S+", "URL": r"https?://\S+"}
    monkeypatch.setattr("piighost.hub.pull", lambda ref, **kwargs: group)
    monkeypatch.setattr(
        "piighost.config.models.detector.pull", lambda ref, **kwargs: group
    )
    app = create_app(FIXTURES / "hub_catalog.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/labels")
    assert response.json()["labels"] == ["EMAIL", "EMPLOYEE_ID", "URL"]


def test_a_config_without_memory_is_served_with_the_in_process_one(
    monkeypatch,
) -> None:
    """A hub configuration declares no memory, so the server supplies one."""
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    app = create_app(FIXTURES / "no_memory.toml")
    payload = {"text": "write a@b.co", "thread_id": "t1"}
    with TestClient(app=app) as client:
        first = client.post("/v1/anonymize", json=payload).json()
        again = client.post("/v1/anonymize", json=payload).json()
    assert first["anonymized_text"] == again["anonymized_text"] == "write <<EMAIL:1>>"
