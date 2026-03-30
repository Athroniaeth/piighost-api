"""Tests for app.py — routes, helpers, lifespan."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.testing import TestClient

from piighost.exceptions import CacheMissError
from piighost.models import Detection, Entity, Span
from piighost.placeholder import CounterPlaceholderFactory

from piighost_api.app import _get_detector_labels, _serialize_entities

from conftest import ENTITY_LOCATION, ENTITY_PERSON


# ------------------------------------------------------------------
# GET /v1/config
# ------------------------------------------------------------------


def test_get_config(client: TestClient) -> None:
    response = client.get("/v1/config")
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == ["PERSON", "LOCATION"]
    assert data["placeholder_factory"] == "CounterPlaceholderFactory"


def test_get_config_no_labels(mock_pipeline: MagicMock, client: TestClient) -> None:
    del mock_pipeline._detector.labels
    response = client.get("/v1/config")
    assert response.status_code == 200
    assert response.json()["labels"] is None


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
    assert data["anonymized_text"] == "<<PERSON_1>> habite à <<LOCATION_1>>"
    assert len(data["entities"]) == 2
    assert data["entities"][0]["label"] == "PERSON"
    assert data["entities"][0]["placeholder"] == "<<PERSON_1>>"
    assert data["entities"][0]["detections"][0]["text"] == "Patrick"


def test_anonymize_custom_thread_id(mock_pipeline: MagicMock, client: TestClient) -> None:
    client.post(
        "/v1/anonymize",
        json={"text": "Patrick habite à Paris", "thread_id": "custom-123"},
    )
    mock_pipeline.anonymize.assert_called_once_with("Patrick habite à Paris", thread_id="custom-123")


# ------------------------------------------------------------------
# POST /v1/deanonymize
# ------------------------------------------------------------------


def test_deanonymize(client: TestClient) -> None:
    response = client.post(
        "/v1/deanonymize",
        json={"text": "<<PERSON_1>> habite à <<LOCATION_1>>"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["text"] == "Patrick habite à Paris"
    assert len(data["entities"]) == 2


def test_deanonymize_cache_miss(mock_pipeline: MagicMock, client: TestClient) -> None:
    mock_pipeline.deanonymize = AsyncMock(side_effect=CacheMissError("not found"))
    response = client.post(
        "/v1/deanonymize",
        json={"text": "<<PERSON_1>> inconnu"},
    )
    assert response.status_code == 404


# ------------------------------------------------------------------
# POST /v1/deanonymize/entities
# ------------------------------------------------------------------


def test_deanonymize_entities(client: TestClient) -> None:
    response = client.post(
        "/v1/deanonymize/entities",
        json={"text": "<<PERSON_1>> aime <<LOCATION_1>>"},
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
    assert result[0].placeholder == "<<PERSON_1>>"
    assert result[1].placeholder == "<<LOCATION_1>>"
    assert result[0].detections[0].text == "Patrick"


def test_serialize_entities_no_match(mock_pipeline: MagicMock) -> None:
    unknown = Entity(
        detections=(Detection("Unknown", "OTHER", Span(0, 7), 0.5),)
    )
    result = _serialize_entities([unknown], mock_pipeline, "default")
    assert result[0].placeholder == ""


def test_get_detector_labels_present(mock_pipeline: MagicMock) -> None:
    assert _get_detector_labels(mock_pipeline) == ["PERSON", "LOCATION"]


def test_get_detector_labels_absent(mock_pipeline: MagicMock) -> None:
    del mock_pipeline._detector.labels
    assert _get_detector_labels(mock_pipeline) is None


# ------------------------------------------------------------------
# Lifespan — auth failure branch
# ------------------------------------------------------------------


def test_lifespan_auth_success() -> None:
    mock_pipeline = MagicMock()
    mock_pipeline._detector = MagicMock()
    mock_pipeline._detector.labels = ["PERSON"]
    mock_pipeline.ph_factory = CounterPlaceholderFactory()
    mock_pipeline.anonymize = AsyncMock(return_value=("anon", []))
    mock_pipeline.get_resolved_entities = MagicMock(return_value=[])

    with patch("piighost_api.app.load_pipeline", return_value=mock_pipeline):
        with patch("piighost_api.app.ApiKeyService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.load_dotenv = AsyncMock()
            mock_svc_cls.return_value = mock_svc

            from piighost_api.app import create_app

            app = create_app("fake:pipeline")

            with TestClient(app=app) as tc:
                response = tc.get("/v1/config")
                assert response.status_code == 200
                mock_svc.load_dotenv.assert_called_once()


def test_lifespan_auth_failure() -> None:
    mock_pipeline = MagicMock()
    mock_pipeline._detector = MagicMock()
    mock_pipeline._detector.labels = ["PERSON"]
    mock_pipeline.ph_factory = CounterPlaceholderFactory()
    mock_pipeline.anonymize = AsyncMock(return_value=("anon", []))
    mock_pipeline.get_resolved_entities = MagicMock(return_value=[])

    with patch("piighost_api.app.load_pipeline", return_value=mock_pipeline):
        with patch.dict("os.environ", {"API_KEY_bad": "invalid-key-format"}, clear=False):
            from piighost_api.app import create_app

            app = create_app("fake:pipeline")

            with TestClient(app=app) as tc:
                response = tc.get("/v1/config")
                assert response.status_code == 200
