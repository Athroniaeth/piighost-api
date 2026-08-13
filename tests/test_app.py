"""Tests for app.py: routes, helpers, lifespan."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.testing import TestClient

from piighost.conversation_memory import MessageRole

from piighost_api.app import _serialize_tokens

from conftest import FIXTURES, TOKENS


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
    from importlib.metadata import version

    assert client.get("/").json()["version"] == version("piighost-api")


# ------------------------------------------------------------------
# GET /health and /v1/labels
# ------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert "detector" in data


def test_health_reports_detector_type(client: TestClient) -> None:
    # The detector comes from the loaded config; the mock declares "regex".
    assert client.get("/health").json()["detector"] == "regex"


# ------------------------------------------------------------------
# POST /v1/anonymize
# ------------------------------------------------------------------


def test_anonymize(client: TestClient) -> None:
    response = client.post("/v1/anonymize", json={"text": "Patrick habite à Paris"})
    assert response.status_code == 201
    data = response.json()
    assert data["anonymized_text"] == "<<PERSON:1>> habite à <<LOCATION:1>>"
    assert len(data["entities"]) == 2
    assert data["entities"][0]["label"] == "PERSON"
    assert data["entities"][0]["placeholder"] == "<<PERSON:1>>"
    assert data["entities"][0]["detections"][0]["text"] == "Patrick"


def test_anonymize_forwards_thread_id_and_role(
    mock_pipeline: MagicMock, client: TestClient
) -> None:
    client.post(
        "/v1/anonymize",
        json={"text": "x", "thread_id": "custom-123", "role": "assistant"},
    )
    mock_pipeline.anonymize.assert_awaited_once_with(
        "x", "custom-123", role=MessageRole.ASSISTANT
    )


def test_anonymize_defaults_role_to_user(
    mock_pipeline: MagicMock, client: TestClient
) -> None:
    client.post("/v1/anonymize", json={"text": "x"})
    mock_pipeline.anonymize.assert_awaited_once_with(
        "x", "default", role=MessageRole.USER
    )


# ------------------------------------------------------------------
# POST /v1/anonymize/corrected
# ------------------------------------------------------------------


def test_anonymize_corrected(mock_pipeline: MagicMock, client: TestClient) -> None:
    response = client.post(
        "/v1/anonymize/corrected",
        json={
            "text": "Patrick habite à Paris",
            "thread_id": "t1",
            "detections": [
                {
                    "text": "Patrick",
                    "label": "PERSON",
                    "start": 0,
                    "end": 7,
                    "confidence": 0.9,
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["anonymized_text"] == "<<PERSON:1>> habite à <<LOCATION:1>>"

    args = mock_pipeline.anonymize_corrected.await_args.args
    assert args[0] == "Patrick habite à Paris"
    assert args[1] == "t1"
    detections = args[2]
    assert len(detections) == 1
    assert detections[0].text == "Patrick"
    assert detections[0].label == "PERSON"
    assert detections[0].span.start == 0
    assert detections[0].span.end == 7


# ------------------------------------------------------------------
# POST /v1/detect
# ------------------------------------------------------------------


def test_detect(mock_pipeline: MagicMock, client: TestClient) -> None:
    response = client.post("/v1/detect", json={"text": "Patrick habite à Paris"})
    assert response.status_code == 201
    data = response.json()
    assert len(data["entities"]) == 2
    assert data["entities"][0]["label"] == "PERSON"
    # A detection preview carries no placeholder.
    assert data["entities"][0]["placeholder"] == ""
    mock_pipeline.detector.detect.assert_awaited_once_with("Patrick habite à Paris")


# ------------------------------------------------------------------
# POST /v1/deanonymize
# ------------------------------------------------------------------


def test_deanonymize(client: TestClient) -> None:
    response = client.post(
        "/v1/deanonymize", json={"text": "<<PERSON:1>> habite à <<LOCATION:1>>"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["text"] == "Patrick habite à Paris"
    # v2 deanonymize returns only the text, no entities.
    assert "entities" not in data


def test_deanonymize_forwards_thread_id(
    mock_pipeline: MagicMock, client: TestClient
) -> None:
    client.post("/v1/deanonymize", json={"text": "x", "thread_id": "t7"})
    mock_pipeline.deanonymize.assert_awaited_once_with("x", "t7")


# ------------------------------------------------------------------
# DELETE /v1/threads/{thread_id}
# ------------------------------------------------------------------


def test_forget_thread_returns_counts(
    client: TestClient, mock_pipeline: MagicMock
) -> None:
    response = client.delete("/v1/threads/t1")
    assert response.status_code == 200
    assert response.json() == {"messages": 2, "detections": 3}
    mock_pipeline.forget_thread.assert_awaited_once_with("t1")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def test_serialize_tokens() -> None:
    result = _serialize_tokens(TOKENS)
    assert len(result) == 2
    assert result[0].label == "PERSON"
    assert result[0].placeholder == "<<PERSON:1>>"
    assert result[0].detections[0].text == "Patrick"
    assert result[0].detections[0].start_pos == 0
    assert result[0].detections[0].end_pos == 7
    assert result[1].placeholder == "<<LOCATION:1>>"


# ------------------------------------------------------------------
# Lifespan: auth branches
# ------------------------------------------------------------------


def _mock_loaders() -> tuple[MagicMock, MagicMock]:
    """Return (mock_config, mock_pipeline) matching create_app's loaders."""
    config = MagicMock()
    config.name = "test"
    config.detector.type = "regex"
    pipeline = MagicMock()
    pipeline.anonymize = AsyncMock()
    return config, pipeline


def test_lifespan_auth_success() -> None:
    config, pipeline = _mock_loaders()
    with (
        patch("piighost_api.app.load_config", return_value=config),
        patch("piighost_api.app.load_thread_pipeline", return_value=pipeline),
        patch("piighost_api.app.ApiKeyService") as mock_svc_cls,
    ):
        mock_svc = MagicMock()
        mock_svc.load_dotenv = AsyncMock()
        mock_svc_cls.return_value = mock_svc

        from piighost_api.app import create_app

        app = create_app(FIXTURES / "minimal.toml")
        with TestClient(app=app) as tc:
            assert tc.get("/v1/labels").status_code == 200
            mock_svc.load_dotenv.assert_called_once()


def test_lifespan_auth_failure() -> None:
    """Bad keys with explicit anonymous opt-in: app boots without auth."""
    config, pipeline = _mock_loaders()
    with (
        patch("piighost_api.app.load_config", return_value=config),
        patch("piighost_api.app.load_thread_pipeline", return_value=pipeline),
        patch.dict(
            "os.environ",
            {"API_KEY_bad": "invalid-key-format", "PIIGHOST_ALLOW_ANONYMOUS": "true"},
            clear=False,
        ),
    ):
        from piighost_api.app import create_app

        app = create_app(FIXTURES / "minimal.toml")
        with TestClient(app=app) as tc:
            assert tc.get("/v1/labels").status_code == 200


# ------------------------------------------------------------------
# Request limits
# ------------------------------------------------------------------


def test_oversized_body_is_rejected(client: TestClient) -> None:
    res = client.post("/v1/anonymize", json={"text": "x" * 2_000_000, "thread_id": "t"})
    # The contract is "rejected, not processed": Litestar returns 413.
    assert res.status_code == 413


def test_rate_limit_throttles_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("PIIGHOST_RATE_LIMIT", "minute:1")
    config, pipeline = _mock_loaders()
    with (
        patch("piighost_api.app.load_config", return_value=config),
        patch("piighost_api.app.load_thread_pipeline", return_value=pipeline),
    ):
        from piighost_api.app import create_app

        app = create_app(FIXTURES / "minimal.toml")

    with TestClient(app=app, raise_server_exceptions=False) as tc:
        assert tc.get("/v1/labels").status_code == 200
        assert tc.get("/v1/labels").status_code == 429
        # Excluded paths are never throttled.
        assert tc.get("/health").status_code == 200


def test_malformed_rate_limit_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed PIIGHOST_RATE_LIMIT must fail loudly at create_app time."""
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    config, pipeline = _mock_loaders()

    for bad in ("minute", "fortnight:5", "minute:0", "minute:-3", "minute:x"):
        monkeypatch.setenv("PIIGHOST_RATE_LIMIT", bad)
        with (
            patch("piighost_api.app.load_config", return_value=config),
            patch("piighost_api.app.load_thread_pipeline", return_value=pipeline),
        ):
            from piighost_api.app import create_app

            with pytest.raises(ValueError, match="PIIGHOST_RATE_LIMIT"):
                create_app(FIXTURES / "minimal.toml")
