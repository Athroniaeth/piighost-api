"""Tests for the OpenAI-proxy pure-relay passthrough routes."""

import httpx
import pytest
import respx
from litestar import Litestar
from litestar.testing import TestClient
from unittest.mock import MagicMock

from piighost_api.routes.openai import build_openai_router


@pytest.fixture
def client() -> TestClient:
    pipeline = MagicMock()
    app = Litestar(route_handlers=[build_openai_router(pipeline)])
    return TestClient(app=app)


@respx.mock
def test_models_is_relayed_untouched(client: TestClient) -> None:
    """GET /models forwards to the upstream and returns its body verbatim."""
    route = respx.get("https://up.example/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
    )
    response = client.get(
        "/openai/v1/models",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer sk-test",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"data": [{"id": "gpt-4o"}]}
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@respx.mock
def test_audio_speech_relays_binary(client: TestClient) -> None:
    """POST /audio/speech forwards the body and returns raw bytes."""
    respx.post("https://up.example/v1/audio/speech").mock(
        return_value=httpx.Response(
            200, content=b"\x00\x01AUDIO", headers={"content-type": "audio/mpeg"}
        )
    )
    response = client.post(
        "/openai/v1/audio/speech",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
        },
        content=b'{"model": "tts-1", "input": "hi", "voice": "alloy"}',
    )
    assert response.status_code == 200
    assert response.content == b"\x00\x01AUDIO"
    assert response.headers["content-type"] == "audio/mpeg"


def test_missing_upstream_header_is_400(client: TestClient) -> None:
    """A relay route without the upstream header returns 400."""
    response = client.get("/openai/v1/models", headers={"authorization": "Bearer x"})
    assert response.status_code == 400
