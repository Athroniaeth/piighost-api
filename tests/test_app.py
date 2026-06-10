"""Tests for app.py — routes, helpers, lifespan."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.testing import TestClient

from piighost.exceptions import CacheMissError
from piighost.models import Detection, Entity, Span
from piighost.placeholder import LabelCounterPlaceholderFactory

from piighost_api.app import _serialize_entities

from conftest import ENTITY_LOCATION, ENTITY_PERSON, FIXTURES


# ------------------------------------------------------------------
# GET /v1/config
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# GET /
# ------------------------------------------------------------------


def test_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "piighost-api"
    assert data["docs"] == "/schema/swagger"


def test_index_reports_package_version(client: TestClient) -> None:
    body = client.get("/").json()
    from importlib.metadata import version

    assert body["version"] == version("piighost-api")


# ------------------------------------------------------------------
# GET /health
# ------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "detector" in data


def test_health_reports_manifest_detectors(client: TestClient) -> None:
    body = client.get("/health").json()
    # Manifest-based, not pipeline._detector: the conftest mock manifest
    # declares its detector types.
    assert body["detector"]
    assert "Mock" not in body["detector"]
    assert body["detector"] == "exact"


# ------------------------------------------------------------------
# POST /v1/anonymize
# ------------------------------------------------------------------


def test_anonymize(client: TestClient) -> None:
    response = client.post(
        "/v1/anonymize",
        json={"text": "Patrick habite à Paris"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["anonymized_text"] == "<<PERSON:1>> habite à <<LOCATION:1>>"
    assert len(data["entities"]) == 2
    assert data["entities"][0]["label"] == "PERSON"
    assert data["entities"][0]["placeholder"] == "<<PERSON:1>>"
    assert data["entities"][0]["detections"][0]["text"] == "Patrick"


def test_anonymize_custom_thread_id(
    mock_pipeline: MagicMock, client: TestClient
) -> None:
    client.post(
        "/v1/anonymize",
        json={"text": "Patrick habite à Paris", "thread_id": "custom-123"},
    )
    mock_pipeline.anonymize.assert_called_once_with(
        "Patrick habite à Paris", thread_id="custom-123"
    )


# ------------------------------------------------------------------
# POST /v1/deanonymize
# ------------------------------------------------------------------


def test_deanonymize(client: TestClient) -> None:
    response = client.post(
        "/v1/deanonymize",
        json={"text": "<<PERSON:1>> habite à <<LOCATION:1>>"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["text"] == "Patrick habite à Paris"
    assert len(data["entities"]) == 2


def test_deanonymize_cache_miss(mock_pipeline: MagicMock, client: TestClient) -> None:
    mock_pipeline.deanonymize = AsyncMock(side_effect=CacheMissError("not found"))
    response = client.post(
        "/v1/deanonymize",
        json={"text": "<<PERSON:1>> inconnu"},
    )
    assert response.status_code == 404


# ------------------------------------------------------------------
# POST /v1/deanonymize/entities
# ------------------------------------------------------------------


def test_deanonymize_entities(client: TestClient) -> None:
    response = client.post(
        "/v1/deanonymize/entities",
        json={"text": "<<PERSON:1>> aime <<LOCATION:1>>"},
    )
    assert response.status_code == 201
    assert response.json()["text"] == "Patrick aime Paris"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def test_serialize_entities(mock_pipeline: MagicMock) -> None:
    entities = [ENTITY_PERSON, ENTITY_LOCATION]
    result = _serialize_entities(entities, mock_pipeline, "default")
    assert len(result) == 2
    assert result[0].placeholder == "<<PERSON:1>>"
    assert result[1].placeholder == "<<LOCATION:1>>"
    assert result[0].detections[0].text == "Patrick"


def test_serialize_entities_no_match(mock_pipeline: MagicMock) -> None:
    unknown = Entity(detections=(Detection("Unknown", "OTHER", Span(0, 7), 0.5),))
    result = _serialize_entities([unknown], mock_pipeline, "default")
    assert result[0].placeholder == ""


# ------------------------------------------------------------------
# Lifespan — auth failure branch
# ------------------------------------------------------------------


def _make_mock_load_pipeline_result() -> tuple[MagicMock, MagicMock]:
    """Return (mock_pipeline, mock_manifest) matching create_app's expectations."""
    pipeline = MagicMock()
    pipeline.ph_factory = LabelCounterPlaceholderFactory()
    pipeline.anonymize = AsyncMock(return_value=("anon", []))
    pipeline.get_resolved_entities = MagicMock(return_value=[])

    manifest = MagicMock()
    manifest.name = "test"
    manifest.schema_version = 1
    manifest.detectors = []

    return pipeline, manifest


def test_lifespan_auth_success() -> None:
    mock_result = _make_mock_load_pipeline_result()

    with patch("piighost_api.app.load_pipeline", return_value=mock_result):
        with patch("piighost_api.app.ApiKeyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.load_dotenv = AsyncMock()
            mock_svc_cls.return_value = mock_svc

            from piighost_api.app import create_app

            app = create_app(FIXTURES / "minimal.toml")

            with TestClient(app=app) as tc:
                response = tc.get("/v1/labels")
                assert response.status_code == 200
                mock_svc.load_dotenv.assert_called_once()


def test_lifespan_auth_failure() -> None:
    """Bad keys with explicit anonymous opt-in: app boots without auth."""
    mock_result = _make_mock_load_pipeline_result()

    with patch("piighost_api.app.load_pipeline", return_value=mock_result):
        with patch.dict(
            "os.environ",
            {"API_KEY_bad": "invalid-key-format", "PIIGHOST_ALLOW_ANONYMOUS": "true"},
            clear=False,
        ):
            from piighost_api.app import create_app

            app = create_app(FIXTURES / "minimal.toml")

            with TestClient(app=app) as tc:
                response = tc.get("/v1/labels")
                assert response.status_code == 200


# ------------------------------------------------------------------
# Request limits
# ------------------------------------------------------------------


def test_oversized_body_is_rejected(client: TestClient) -> None:
    res = client.post("/v1/anonymize", json={"text": "x" * 2_000_000, "thread_id": "t"})
    # The contract is "rejected, not processed": Litestar returns 413
    # when the body exceeds request_max_body_size.
    assert res.status_code == 413


def test_rate_limit_throttles_second_request(
    monkeypatch, mock_pipeline: MagicMock
) -> None:
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("PIIGHOST_RATE_LIMIT", "minute:1")
    mock_result = _make_mock_load_pipeline_result()

    with patch("piighost_api.app.load_pipeline", return_value=mock_result):
        from piighost_api.app import create_app

        app = create_app(FIXTURES / "minimal.toml")

    with TestClient(app=app, raise_server_exceptions=False) as tc:
        assert tc.get("/v1/labels").status_code == 200
        assert tc.get("/v1/labels").status_code == 429
        # Excluded paths are never throttled.
        assert tc.get("/health").status_code == 200


def test_malformed_rate_limit_raises_clear_error(
    monkeypatch, mock_pipeline: MagicMock
) -> None:
    """A malformed PIIGHOST_RATE_LIMIT must fail loudly at create_app time."""
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    mock_result = _make_mock_load_pipeline_result()

    for bad in ("minute", "fortnight:5", "minute:0", "minute:-3", "minute:x"):
        monkeypatch.setenv("PIIGHOST_RATE_LIMIT", bad)
        with patch("piighost_api.app.load_pipeline", return_value=mock_result):
            from piighost_api.app import create_app

            with pytest.raises(ValueError, match="PIIGHOST_RATE_LIMIT"):
                create_app(FIXTURES / "minimal.toml")


# ------------------------------------------------------------------
# DELETE /v1/threads/{thread_id}
# ------------------------------------------------------------------


def test_forget_thread_returns_204(client, mock_pipeline):
    res = client.delete("/v1/threads/t1")
    assert res.status_code == 204
    mock_pipeline.forget_thread.assert_awaited_once_with("t1")
