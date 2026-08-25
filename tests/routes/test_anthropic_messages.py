"""Integration tests for the Anthropic-proxy messages route."""

import httpx
import pytest
import respx
from litestar import Litestar
from litestar.testing import TestClient

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes.anthropic import (
    DEFAULT_PLACEHOLDER_NOTE,
    build_anthropic_router,
)

_DEFAULT = "https://api.anthropic.com/v1"
"""Default upstream URL used when no X-PIIGhost-Upstream header is supplied."""

_HEADERS = {
    "x-api-key": "sk-ant",
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}
"""Minimal Anthropic auth headers sent with every test request."""


@pytest.fixture
def client() -> TestClient:
    """TestClient over a Litestar app with the Anthropic router and a known detector."""
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    # No placeholder note here: it embeds an example <<PERSON:1>>, which would make
    # the token-presence assertions trivially pass. The note has its own tests.
    router = build_anthropic_router(
        pipeline, default_upstream=_DEFAULT, placeholder_note=None
    )
    app = Litestar(route_handlers=[router])
    return TestClient(app=app)


@respx.mock
def test_upstream_sees_tokens_reply_is_restored(client: TestClient) -> None:
    """The model sees <<PERSON:1>>, never Patrick; the reply is restored."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi <<PERSON:1>>"}],
            },
        )
    )
    response = client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "I am Patrick"}],
        },
    )
    assert response.status_code == 200
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded
    assert "<<PERSON:1>>" in forwarded
    assert response.json()["content"][0]["text"] == "Hi Patrick"


@respx.mock
def test_system_prompt_is_anonymized(client: TestClient) -> None:
    """The system prompt is anonymized before forwarding to the upstream."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"type": "message", "role": "assistant", "content": []},
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "system": "You help Patrick.",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded


@respx.mock
def test_tool_result_anonymized_and_tool_use_restored(client: TestClient) -> None:
    """A tool_result is anonymized on the way in; a tool_use input is restored back."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_2",
                        "name": "run",
                        "input": {"cmd": "echo <<PERSON:1>>"},
                    }
                ],
            },
        )
    )
    response = client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "Patrick was here",
                        }
                    ],
                }
            ],
        },
    )
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded
    assert response.json()["content"][0]["input"]["cmd"] == "echo Patrick"


@respx.mock
def test_image_block_passthrough(client: TestClient) -> None:
    """Image content blocks pass through to the upstream without modification."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": [image]}],
        },
    )
    forwarded = route.calls.last.request.content.decode()
    assert '"data": "AAAA"' in forwarded or '"data":"AAAA"' in forwarded


@respx.mock
def test_default_upstream_used_without_header(client: TestClient) -> None:
    """No X-PIIGhost-Upstream header falls back to the configured default."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert route.called


@respx.mock
def test_upstream_header_overrides_default(client: TestClient) -> None:
    """X-PIIGhost-Upstream header overrides the router's configured default upstream."""
    route = respx.post("https://custom.example/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers={**_HEADERS, "x-piighost-upstream": "https://custom.example/v1"},
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert route.called


@respx.mock
def test_headers_forwarded_and_piighost_dropped(client: TestClient) -> None:
    """Anthropic auth headers are forwarded; PIIGhost-specific headers are stripped."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers={**_HEADERS, "x-piighost-thread-id": "t9"},
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    sent = route.calls.last.request.headers
    assert sent["x-api-key"] == "sk-ant"
    assert sent["anthropic-version"] == "2023-06-01"
    assert "x-piighost-thread-id" not in sent
    assert "x-piighost-upstream" not in sent


@respx.mock
def test_count_tokens_request_is_anonymized(client: TestClient) -> None:
    """count_tokens requests are anonymized before forwarding and the token count is returned."""
    route = respx.post("https://api.anthropic.com/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 7})
    )
    response = client.post(
        "/anthropic/v1/messages/count_tokens",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "I am Patrick"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["input_tokens"] == 7
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded
    assert "<<PERSON:1>>" in forwarded


@respx.mock
def test_stream_restores_token_split_across_deltas(client: TestClient) -> None:
    """A <<PERSON:1>> split over two content_block_delta events is restored."""
    sse = (
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"Hi <<PER"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"SON:1>>"}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, content=sse, headers={"content-type": "text/event-stream"}
        )
    )
    headers = {**_HEADERS, "x-piighost-thread-id": "t1"}
    # Prime the fixed thread so <<PERSON:1>> maps to Patrick.
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, content=sse, headers={"content-type": "text/event-stream"}
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers=headers,
        json={
            "model": "m",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "Patrick"}],
        },
    )
    with client.stream(
        "POST",
        "/anthropic/v1/messages",
        headers=headers,
        json={
            "model": "m",
            "max_tokens": 8,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as response:
        received = b"".join(response.iter_bytes()).decode()
    assert "Patrick" in received
    assert "<<PERSON" not in received


@respx.mock
def test_system_preserved_when_disabled_and_headers_relayed() -> None:
    """With anonymize_system=False the system stays intact while messages anonymize; OAuth headers relay."""
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    router = build_anthropic_router(
        pipeline,
        default_upstream=_DEFAULT,
        anonymize_system=False,
        placeholder_note=None,
    )
    app = Litestar(route_handlers=[router])
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    with TestClient(app=app) as tc:
        tc.post(
            "/anthropic/v1/messages",
            headers={
                "authorization": "Bearer oauth",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "user-agent": "claude-cli/1.2.3",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-sonnet",
                "max_tokens": 64,
                "system": "You help Patrick.",
                "messages": [{"role": "user", "content": "I am Patrick"}],
            },
        )
    forwarded_body = route.calls.last.request.content.decode()
    forwarded_headers = route.calls.last.request.headers
    assert "You help Patrick." in forwarded_body
    assert "I am Patrick" not in forwarded_body
    assert "<<PERSON:1>>" in forwarded_body
    assert forwarded_headers["user-agent"] == "claude-cli/1.2.3"
    assert forwarded_headers["authorization"] == "Bearer oauth"


@respx.mock
def test_placeholder_note_prepended_to_system() -> None:
    """The guidance note is prepended to the system prompt the upstream receives."""
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    router = build_anthropic_router(pipeline, default_upstream=_DEFAULT)
    app = Litestar(route_handlers=[router])
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    with TestClient(app=app) as tc:
        tc.post(
            "/anthropic/v1/messages",
            headers=_HEADERS,
            json={
                "model": "claude-3-5-sonnet",
                "max_tokens": 16,
                "system": "You are a helpful assistant.",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    forwarded = route.calls.last.request.content.decode()
    assert DEFAULT_PLACEHOLDER_NOTE in forwarded
    assert "You are a helpful assistant." in forwarded


@respx.mock
def test_placeholder_note_absent_when_disabled(client: TestClient) -> None:
    """With placeholder_note=None (the fixture), no note reaches the upstream."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"type": "message", "role": "assistant", "content": []}
        )
    )
    client.post(
        "/anthropic/v1/messages",
        headers=_HEADERS,
        json={
            "model": "claude-3-5-sonnet",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    forwarded = route.calls.last.request.content.decode()
    assert "Privacy note" not in forwarded
