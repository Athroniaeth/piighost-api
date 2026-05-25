"""Tests for app.py — routes, helpers, lifespan."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    assert data["version"] == "0.1.0"
    assert data["docs"] == "/schema/swagger"


# ------------------------------------------------------------------
# GET /health
# ------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "detector" in data


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
    pipeline._detector = MagicMock()
    pipeline._detector.labels = ["PERSON"]
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
    mock_result = _make_mock_load_pipeline_result()

    with patch("piighost_api.app.load_pipeline", return_value=mock_result):
        with patch.dict(
            "os.environ", {"API_KEY_bad": "invalid-key-format"}, clear=False
        ):
            from piighost_api.app import create_app

            app = create_app(FIXTURES / "minimal.toml")

            with TestClient(app=app) as tc:
                response = tc.get("/v1/labels")
                assert response.status_code == 200
