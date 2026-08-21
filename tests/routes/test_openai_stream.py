"""Tests for the OpenAI-proxy chat/completions streaming path."""

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


@respx.mock
def test_stream_restores_a_token_split_across_chunks(client: TestClient) -> None:
    """A <<PERSON:1>> split over two SSE chunks is restored to Patrick in the stream."""
    sse = (
        'data: {"choices":[{"delta":{"content":"Hi <<PER"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"SON:1>>"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse, headers={"content-type": "text/event-stream"}
        )
    )
    headers = {
        "x-piighost-upstream": "https://up.example/v1",
        "authorization": "Bearer x",
        "content-type": "application/json",
        "x-piighost-thread-id": "t1",
    }
    # Prime the fixed thread so <<PERSON:1>> maps to Patrick.
    client.post(
        "/openai/v1/chat/completions",
        headers=headers,
        json={"model": "m", "messages": [{"role": "user", "content": "Patrick"}]},
    )
    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers=headers,
        json={
            "model": "m",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as response:
        received = b"".join(response.iter_bytes()).decode()
    assert "Patrick" in received
    assert "<<PERSON" not in received


@respx.mock
def test_stream_ending_mid_token_flushes_the_fragment(client: TestClient) -> None:
    """A stream ending inside an unclosed token flushes the fragment, no loss, no PII."""
    sse = 'data: {"choices":[{"delta":{"content":"Hi <<PER"}}]}\n\ndata: [DONE]\n\n'
    respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse, headers={"content-type": "text/event-stream"}
        )
    )
    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer x",
            "content-type": "application/json",
        },
        json={
            "model": "m",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as response:
        received = b"".join(response.iter_bytes()).decode()
    # The incomplete token is emitted unreplaced by flush, not silently dropped;
    # a partial token carries no real value, so nothing leaks.
    assert "<<PER" in received
