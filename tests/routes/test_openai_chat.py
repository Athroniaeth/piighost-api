"""Tests for the OpenAI-proxy chat/completions route (non-streaming)."""

import httpx
import pytest
import respx
from litestar import Litestar
from litestar.testing import TestClient

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes.openai import build_openai_router


@pytest.fixture
def client() -> TestClient:
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    app = Litestar(route_handlers=[build_openai_router(pipeline)])
    return TestClient(app=app)


def test_non_object_body_is_400(client: TestClient) -> None:
    """A valid-JSON but non-object body is a 400, not a 500."""
    response = client.post(
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer x",
            "content-type": "application/json",
        },
        json=[1, 2, 3],
    )
    assert response.status_code == 400


@respx.mock
def test_upstream_sees_tokens_reply_is_restored(client: TestClient) -> None:
    """The upstream receives tokens; the returned reply is restored."""
    route = respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "Hi <<PERSON:1>>"}}
                ]
            },
        )
    )
    response = client.post(
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "I am Patrick"}],
        },
    )
    assert response.status_code == 200
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded
    assert "<<PERSON:1>>" in forwarded
    assert response.json()["choices"][0]["message"]["content"] == "Hi Patrick"


@respx.mock
def test_upstream_error_status_is_relayed(client: TestClient) -> None:
    """An upstream 4xx is relayed with its status."""
    respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )
    response = client.post(
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
        },
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 429


@respx.mock
def test_ephemeral_thread_is_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no thread header, the per-request thread is forgotten after the reply."""
    from unittest.mock import AsyncMock

    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    forget = AsyncMock(wraps=pipeline.forget_thread)
    monkeypatch.setattr(pipeline, "forget_thread", forget)
    app = Litestar(route_handlers=[build_openai_router(pipeline)])
    respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
    )
    with TestClient(app=app) as tc:
        tc.post(
            "/openai/v1/chat/completions",
            headers={
                "x-piighost-upstream": "https://up.example/v1",
                "authorization": "Bearer x",
                "content-type": "application/json",
            },
            json={"model": "m", "messages": [{"role": "user", "content": "Patrick"}]},
        )
    forget.assert_awaited_once()
