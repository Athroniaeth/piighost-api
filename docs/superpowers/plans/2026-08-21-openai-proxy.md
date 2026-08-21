# OpenAI-compatible Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenAI-compatible proxy under `/openai/v1` to piighost-api that anonymizes requests, relays them to a caller-chosen upstream, and deanonymizes replies, so the upstream provider only ever sees tokens.

**Architecture:** A Litestar `Router(path="/openai/v1")` mounted in `create_app`, whose handlers close over the existing `ThreadAnonymizationPipeline`. Text routes rewrite only known fields (via the pipeline's public `anonymize`/`deanonymize`) and relay through httpx; multimodal and metadata routes are byte passthroughs. Streaming uses the library's `AsyncPlaceholderStreamDecoder`. Everything lives in piighost-api over the library's public API; no library change.

**Tech Stack:** Python 3.12+, Litestar 2.16, msgspec, httpx (new), piighost lib (public API), pytest + respx (new dev dep). Repo: piighost-api, branch `feat/openai-proxy`.

**Spec:** `docs/superpowers/specs/2026-08-21-openai-proxy-design.md`

---

## File Structure

- `pyproject.toml` — add `httpx` (runtime) and `respx` (dev); bump `piighost` pin to `>=1.3`.
- `src/piighost_api/routes/__init__.py` (create) — package marker.
- `src/piighost_api/routes/_upstream.py` (create) — upstream URL/header helpers + a shared `httpx.AsyncClient`.
- `src/piighost_api/routes/_rewrite.py` (create) — pure body anonymize/deanonymize functions over the pipeline.
- `src/piighost_api/routes/openai.py` (create) — the handlers and `build_openai_router(pipeline) -> Router`.
- `src/piighost_api/app.py` (modify) — mount the router.
- `tests/routes/test_upstream.py`, `test_rewrite.py`, `test_openai_relay.py`, `test_openai_chat.py`, `test_openai_stream.py`, `test_openai_text.py` (create).
- `docs/en/**`, `docs/fr/**`, `README.md`, `README.fr.md` (modify) — proxy docs.
- (cross-repo) `~/PycharmProjects/piighost/docs/en/roadmap.md` + `docs/fr/roadmap.md` (modify) — lib/proxy boundary.

Litestar notes used throughout: a handler takes `request: Request` (`from litestar import Request`), reads the raw body with `await request.body()` or `await request.json()`, reads headers with `request.headers.get("x-...")`, returns a `Response(content=..., status_code=..., media_type=...)` (`from litestar import Response`) or a `Stream(gen, media_type=..., status_code=...)` (`from litestar.response import Stream`), and raises `HTTPException(status_code=..., detail=...)` (`from litestar.exceptions import HTTPException`). Proxy handlers carry `exclude_from_auth=True` in their decorator, since the transparent relay forwards the caller's own upstream key.

---

## Task 1: Dependencies and upstream helpers

**Files:**
- Modify: `pyproject.toml`
- Create: `src/piighost_api/routes/__init__.py`
- Create: `src/piighost_api/routes/_upstream.py`
- Test: `tests/routes/test_upstream.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, in `[project] dependencies`, bump the piighost pin and add httpx:

```toml
    "piighost[config,redis]>=1.3",
    "httpx>=0.28",
```

In the dev dependency group (where `pytest` etc. live), add:

```toml
    "respx>=0.22",
```

Run: `uv lock` then `uv sync`. Expected: httpx and respx resolve.

- [ ] **Step 2: Create the routes package**

Create `src/piighost_api/routes/__init__.py`:

```python
"""HTTP route families for piighost-api beyond the core /v1 endpoints."""
```

- [ ] **Step 3: Write the failing test**

Create `tests/routes/test_upstream.py`:

```python
"""Tests for the OpenAI-proxy upstream helpers."""

import pytest
from litestar.exceptions import HTTPException

from piighost_api.routes._upstream import forward_headers, upstream_base_url


class _Headers:
    """A minimal stand-in for Litestar's request.headers mapping."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)


def test_upstream_base_url_reads_the_header() -> None:
    """The upstream base URL comes from X-PIIGhost-Upstream, trailing slash trimmed."""
    headers = _Headers({"x-piighost-upstream": "https://api.openai.com/v1/"})
    assert upstream_base_url(headers) == "https://api.openai.com/v1"


def test_upstream_base_url_missing_raises_400() -> None:
    """A missing upstream header is a 400."""
    headers = _Headers({})
    with pytest.raises(HTTPException) as excinfo:
        upstream_base_url(headers)
    assert excinfo.value.status_code == 400


def test_forward_headers_keeps_auth_and_content_type_only() -> None:
    """Only Authorization and Content-Type are forwarded; piighost headers dropped."""
    headers = _Headers(
        {
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
            "x-piighost-upstream": "https://api.openai.com/v1",
            "x-piighost-thread-id": "t1",
            "host": "proxy.local",
        }
    )
    assert forward_headers(headers) == {
        "authorization": "Bearer sk-test",
        "content-type": "application/json",
    }
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_upstream.py -v`
Expected: FAIL importing `_upstream` (module does not exist).

- [ ] **Step 5: Implement the helpers**

Create `src/piighost_api/routes/_upstream.py`:

```python
"""Helpers for the OpenAI proxy's outbound relay.

The upstream is chosen per request via the X-PIIGhost-Upstream header, and the
caller's Authorization is forwarded to it, so the proxy is a transparent relay.
"""

from typing import Protocol

from litestar.exceptions import HTTPException

UPSTREAM_HEADER = "x-piighost-upstream"
THREAD_HEADER = "x-piighost-thread-id"

_FORWARDED = ("authorization", "content-type")


class _HeaderMap(Protocol):
    """The subset of a headers mapping these helpers read."""

    def get(self, key: str, default: str | None = None) -> str | None: ...


def upstream_base_url(headers: _HeaderMap) -> str:
    """Return the upstream base URL from the header, or raise 400 when absent."""
    raw = headers.get(UPSTREAM_HEADER)
    if not raw:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Missing {UPSTREAM_HEADER} header. Set it to an "
                "OpenAI-compatible base URL, e.g. https://api.openai.com/v1."
            ),
        )
    return raw.rstrip("/")


def forward_headers(headers: _HeaderMap) -> dict[str, str]:
    """Keep only the headers the upstream needs, dropping piighost and hop-by-hop."""
    forwarded: dict[str, str] = {}
    for name in _FORWARDED:
        value = headers.get(name)
        if value is not None:
            forwarded[name] = value
    return forwarded
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_upstream.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/piighost_api/routes/__init__.py src/piighost_api/routes/_upstream.py tests/routes/test_upstream.py
git commit -m "feat(proxy): upstream helpers and httpx/respx deps"
```

---

## Task 2: Body rewrite core

**Files:**
- Create: `src/piighost_api/routes/_rewrite.py`
- Test: `tests/routes/test_rewrite.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_rewrite.py`. It uses a real offline `ThreadAnonymizationPipeline` over an `ExactMatchDetector`, so no model or network is involved:

```python
"""Tests for the OpenAI-proxy body rewriting, over an offline pipeline."""

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    deanonymize_chat_response,
)


def _pipeline() -> ThreadAnonymizationPipeline:
    detector = ExactMatchDetector({"Patrick": "PERSON", "Paris": "LOCATION"})
    return ThreadAnonymizationPipeline(detector)


async def test_anonymize_chat_request_rewrites_message_content() -> None:
    """Every message's string content is anonymized into the thread."""
    pipeline = _pipeline()
    body = {"messages": [{"role": "user", "content": "Patrick lives in Paris"}]}
    result = await anonymize_chat_request(body, pipeline, "t")
    assert result["messages"][0]["content"] == "<<PERSON:1>> lives in <<LOCATION:1>>"


async def test_anonymize_chat_request_rewrites_tool_call_arguments() -> None:
    """A tool_call's JSON arguments have their string values anonymized."""
    pipeline = _pipeline()
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "send",
                            "arguments": '{"to": "Patrick"}',
                        },
                    }
                ],
            }
        ]
    }
    result = await anonymize_chat_request(body, pipeline, "t")
    args = result["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert args == '{"to": "<<PERSON:1>>"}'


async def test_deanonymize_chat_response_restores_content_and_tool_args() -> None:
    """The reply's content and tool_call arguments are restored."""
    pipeline = _pipeline()
    # Prime the thread so the tokens are known.
    await anonymize_chat_request(
        {"messages": [{"role": "user", "content": "Patrick in Paris"}]},
        pipeline,
        "t",
    )
    response = {
        "choices": [
            {
                "message": {
                    "content": "<<PERSON:1>> is in <<LOCATION:1>>",
                    "tool_calls": [
                        {"function": {"arguments": '{"who": "<<PERSON:1>>"}'}}
                    ],
                }
            }
        ]
    }
    result = await deanonymize_chat_response(response, pipeline, "t")
    message = result["choices"][0]["message"]
    assert message["content"] == "Patrick is in Paris"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"who": "Patrick"}'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_rewrite.py -v`
Expected: FAIL importing `_rewrite`.

- [ ] **Step 3: Implement the rewrite core**

Create `src/piighost_api/routes/_rewrite.py`:

```python
"""Field-level anonymize and deanonymize for OpenAI request and response bodies.

Only known text fields are rewritten; everything else is forwarded untouched, so
the proxy stays robust to the OpenAI schema evolving. All rewriting goes through
the pipeline's public anonymize and deanonymize, over a single thread per request.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from piighost.pipeline import ThreadAnonymizationPipeline

_StringOp = Callable[[str], Awaitable[str]]


async def _map_strings(value: Any, op: _StringOp) -> Any:
    """Apply op to every string inside nested dicts and lists, in place of value."""
    if isinstance(value, str):
        return await op(value)
    if isinstance(value, dict):
        return {key: await _map_strings(item, op) for key, item in value.items()}
    if isinstance(value, list):
        return [await _map_strings(item, op) for item in value]
    return value


async def _rewrite_json_string(raw: str, op: _StringOp) -> str:
    """Rewrite the string values inside a JSON string, or the string itself.

    A tool_call's arguments are a JSON string. When it parses, rewrite every
    string it holds and re-serialize; when it does not, treat it as plain text.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return await op(raw)
    rewritten = await _map_strings(parsed, op)
    return json.dumps(rewritten)


async def _rewrite_content(content: Any, op: _StringOp) -> Any:
    """Rewrite a message content, a string or a list of typed content parts."""
    if isinstance(content, str):
        return await op(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part = {**part, "text": await op(part["text"])}
            parts.append(part)
        return parts
    return content


async def _rewrite_messages(messages: Any, op: _StringOp) -> None:
    """Rewrite message content and tool_call arguments in place."""
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        if "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                function["arguments"] = await _rewrite_json_string(
                    function["arguments"], op
                )


def _anonymizer(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> _StringOp:
    """A string op that anonymizes into the thread."""

    async def op(text: str) -> str:
        result = await pipeline.anonymize(text, thread_id)
        return result.text

    return op


def _deanonymizer(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> _StringOp:
    """A string op that deanonymizes from the thread."""

    async def op(text: str) -> str:
        return await pipeline.deanonymize(text, thread_id)

    return op


async def anonymize_chat_request(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize a chat/completions request body's messages and tool_call args."""
    await _rewrite_messages(body.get("messages"), _anonymizer(pipeline, thread_id))
    return body


async def deanonymize_chat_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a chat/completions response body's choices."""
    op = _deanonymizer(pipeline, thread_id)
    for choice in body.get("choices") or []:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        if "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                function["arguments"] = await _rewrite_json_string(
                    function["arguments"], op
                )
    return body


async def anonymize_input_field(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize the `input` field (string or list) for embeddings and moderations."""
    if "input" in body:
        body["input"] = await _map_strings(
            body["input"], _anonymizer(pipeline, thread_id)
        )
    return body


async def anonymize_prompt_field(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize the `prompt` field (string or list) for legacy completions."""
    if "prompt" in body:
        body["prompt"] = await _map_strings(
            body["prompt"], _anonymizer(pipeline, thread_id)
        )
    return body


async def deanonymize_completion_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a legacy completions response body's choices[].text."""
    op = _deanonymizer(pipeline, thread_id)
    for choice in body.get("choices") or []:
        if isinstance(choice, dict) and isinstance(choice.get("text"), str):
            choice["text"] = await op(choice["text"])
    return body
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_rewrite.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/_rewrite.py tests/routes/test_rewrite.py
git commit -m "feat(proxy): body anonymize/deanonymize rewrite core"
```

---

## Task 3: Router scaffold and pure-relay passthrough

**Files:**
- Create: `src/piighost_api/routes/openai.py`
- Test: `tests/routes/test_openai_relay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_openai_relay.py`. It builds a Litestar app mounting only the openai router over a mock pipeline, and mocks the upstream with respx:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_openai_relay.py -v`
Expected: FAIL importing `build_openai_router`.

- [ ] **Step 3: Implement the router scaffold and passthrough**

Create `src/piighost_api/routes/openai.py` with the shared client, the passthrough handlers, and the router factory. Later tasks add the anonymizing handlers to the same file and router list.

```python
"""OpenAI-compatible proxy routes.

A transparent relay: the caller points its base_url at /openai/v1 and adds an
X-PIIGhost-Upstream header naming the real OpenAI-compatible endpoint. Text routes
anonymize the request, relay it, and deanonymize the reply; metadata and
multimodal routes are byte passthroughs. The caller's Authorization is forwarded
to the upstream, so these routes carry exclude_from_auth.
"""

from typing import Any

import httpx
from litestar import Request, Response, Router, get, post
from litestar.exceptions import HTTPException

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._upstream import forward_headers, upstream_base_url

_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))


async def _relay_raw(request: Request, subpath: str) -> Response:
    """Forward the request to the upstream verbatim and return its raw response."""
    base = upstream_base_url(request.headers)
    headers = forward_headers(request.headers)
    body = await request.body()
    try:
        upstream = await _client.request(
            request.method,
            f"{base}/{subpath}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
    media_type = upstream.headers.get("content-type")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=media_type,
    )


def build_openai_router(pipeline: ThreadAnonymizationPipeline) -> Router:
    """Build the /openai/v1 router over the given pipeline."""

    @get(["/models", "/models/{model:str}"], exclude_from_auth=True)
    async def models(request: Request) -> Response:
        return await _relay_raw(request, request.url.path.split("/openai/v1/", 1)[1])

    @post(
        [
            "/images/generations",
            "/images/edits",
            "/images/variations",
            "/audio/speech",
            "/audio/transcriptions",
            "/audio/translations",
        ],
        exclude_from_auth=True,
    )
    async def multimodal(request: Request) -> Response:
        return await _relay_raw(request, request.url.path.split("/openai/v1/", 1)[1])

    return Router(path="/openai/v1", route_handlers=[models, multimodal])
```

Note for the implementer: verify Litestar exposes the matched sub-path cleanly. `request.url.path` is the full path; splitting on `/openai/v1/` yields the subpath. If Litestar offers a tidier accessor (e.g. a path param), prefer it, but the split is correct and covered by the test.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_openai_relay.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/openai.py tests/routes/test_openai_relay.py
git commit -m "feat(proxy): router scaffold and pure-relay passthrough"
```

---

## Task 4: chat/completions (non-streaming) with threading

**Files:**
- Modify: `src/piighost_api/routes/openai.py`
- Test: `tests/routes/test_openai_chat.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_openai_chat.py`. It uses a real offline pipeline so anonymize/deanonymize actually run, and respx to capture what the upstream received:

```python
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
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "I am Patrick"}]},
    )
    assert response.status_code == 200
    # The upstream never saw "Patrick".
    forwarded = route.calls.last.request.content.decode()
    assert "Patrick" not in forwarded
    assert "<<PERSON:1>>" in forwarded
    # The caller gets the real value back.
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_openai_chat.py -v`
Expected: FAIL (no chat/completions route yet -> 404).

- [ ] **Step 3: Add threading helpers and the chat handler**

In `src/piighost_api/routes/openai.py`, add these imports at the top:

```python
import json
import uuid

from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    deanonymize_chat_response,
)
```

Add a threading helper above `build_openai_router`:

```python
def _resolve_thread(request: Request) -> tuple[str, bool]:
    """Return (thread_id, ephemeral): a supplied fixed id, or a fresh ephemeral one."""
    supplied = request.headers.get("x-piighost-thread-id")
    if supplied:
        return supplied, False
    return uuid.uuid4().hex, True


async def _forward_json(base: str, subpath: str, headers: dict[str, str], body: dict) -> httpx.Response:
    """POST a JSON body to the upstream, mapping transport errors to 502."""
    try:
        return await _client.post(f"{base}/{subpath}", headers=headers, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
```

Inside `build_openai_router`, add the handler and register it:

```python
    @post("/chat/completions", exclude_from_auth=True)
    async def chat_completions(request: Request) -> Response:
        base = upstream_base_url(request.headers)
        headers = forward_headers(request.headers)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body must be JSON.")
        thread_id, ephemeral = _resolve_thread(request)
        try:
            if body.get("stream"):
                raise HTTPException(
                    status_code=400,
                    detail="Streaming is handled by the streaming path.",
                )
            await anonymize_chat_request(body, pipeline, thread_id)
            upstream = await _forward_json(base, "chat/completions", headers, body)
            payload = upstream.json() if upstream.headers.get("content-type", "").startswith("application/json") else None
            if payload is not None and upstream.status_code < 400:
                await deanonymize_chat_response(payload, pipeline, thread_id)
                return Response(content=payload, status_code=upstream.status_code)
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
            )
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)
```

Update the router's `route_handlers` list to include `chat_completions`:

```python
    return Router(
        path="/openai/v1",
        route_handlers=[chat_completions, models, multimodal],
    )
```

The `if body.get("stream")` guard raises 400 for now; Task 5 replaces it with the streaming path. The `json`/`uuid` imports are used here and in Task 5.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_openai_chat.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/openai.py tests/routes/test_openai_chat.py
git commit -m "feat(proxy): chat/completions non-streaming with ephemeral threading"
```

---

## Task 5: chat/completions streaming

**Files:**
- Modify: `src/piighost_api/routes/openai.py`
- Test: `tests/routes/test_openai_stream.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_openai_stream.py`. It streams an SSE body from the mocked upstream with a token split across two chunks, and asserts the client receives the restored text:

```python
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
    # Prime the thread with a fixed id so <<PERSON:1>> maps to Patrick.
    client.post(
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer x",
            "content-type": "application/json",
            "x-piighost-thread-id": "t1",
        },
        json={"model": "m", "messages": [{"role": "user", "content": "Patrick"}]},
    )
    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer x",
            "content-type": "application/json",
            "x-piighost-thread-id": "t1",
        },
        json={
            "model": "m",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as response:
        received = b"".join(response.iter_bytes()).decode()
    assert "Patrick" in received
    assert "<<PERSON" not in received
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_openai_stream.py -v`
Expected: FAIL (streaming currently raises 400).

- [ ] **Step 3: Implement the streaming path**

In `src/piighost_api/routes/openai.py`, add imports:

```python
from collections.abc import AsyncIterator

from litestar.response import Stream

from piighost.components.placeholder import AsyncPlaceholderStreamDecoder
```

Add the SSE restore coroutine above `build_openai_router`:

```python
async def _restore_sse_chunk(
    raw: str, decoder: AsyncPlaceholderStreamDecoder
) -> str:
    """Restore tokens in an SSE `data:` payload's delta content.

    Non-data lines and the [DONE] sentinel pass through. For a data line, the
    JSON delta's content is fed through the decoder, which reassembles a token
    split across chunks, and the line is re-serialized.
    """
    if not raw.startswith("data:"):
        return raw
    payload = raw[len("data:") :].strip()
    if payload == "[DONE]" or not payload:
        return raw
    try:
        event = json.loads(payload)
    except ValueError:
        return raw
    for choice in event.get("choices") or []:
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            delta["content"] = await decoder.feed(delta["content"])
    return "data: " + json.dumps(event)
```

Add the stream builder:

```python
def _stream_upstream(
    base: str,
    headers: dict[str, str],
    body: dict,
    pipeline: ThreadAnonymizationPipeline,
    thread_id: str,
    ephemeral: bool,
) -> AsyncIterator[bytes]:
    """Yield restored SSE bytes from the upstream stream, then forget if ephemeral."""

    async def generator() -> AsyncIterator[bytes]:
        async def replace(token: str) -> str:
            return await pipeline.deanonymize(token, thread_id)

        decoder = AsyncPlaceholderStreamDecoder(pipeline.recognizer, replace)
        try:
            async with _client.stream(
                "POST", f"{base}/chat/completions", headers=headers, json=body
            ) as upstream:
                async for line in upstream.aiter_lines():
                    restored = await _restore_sse_chunk(line, decoder)
                    yield (restored + "\n").encode()
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return generator()
```

Note for the implementer: confirm the `AsyncPlaceholderStreamDecoder` constructor signature against the installed piighost (it takes the pipeline's token recognizer and an async `replace` callback; `pipeline.recognizer` exposes it). Adjust the construction to the real signature if it differs, keeping the feed-per-delta behavior. `aiter_lines()` drops the blank SSE separators, so this re-emits one line per event; if byte-exact SSE framing matters to a client, switch to `aiter_raw()` and split on `\n\n`.

In the `chat_completions` handler, replace the streaming 400 guard with the streaming return. The block that currently reads:

```python
            if body.get("stream"):
                raise HTTPException(
                    status_code=400,
                    detail="Streaming is handled by the streaming path.",
                )
            await anonymize_chat_request(body, pipeline, thread_id)
```

becomes:

```python
            await anonymize_chat_request(body, pipeline, thread_id)
            if body.get("stream"):
                stream = _stream_upstream(
                    base, headers, body, pipeline, thread_id, ephemeral
                )
                ephemeral = False  # the generator owns the forget now
                return Stream(stream, media_type="text/event-stream")
```

The `ephemeral = False` reassignment stops the handler's `finally` from forgetting the thread before the stream has finished; the generator's own `finally` does it after the stream closes.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_openai_stream.py -v`
Expected: PASS (1 passed). If the decoder constructor differs, fix per the note and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/openai.py tests/routes/test_openai_stream.py
git commit -m "feat(proxy): chat/completions streaming with token reassembly"
```

---

## Task 6: completions, embeddings, moderations

**Files:**
- Modify: `src/piighost_api/routes/openai.py`
- Test: `tests/routes/test_openai_text.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_openai_text.py`:

```python
"""Tests for the OpenAI-proxy legacy completions, embeddings, moderations."""

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


def _headers() -> dict[str, str]:
    return {
        "x-piighost-upstream": "https://up.example/v1",
        "authorization": "Bearer x",
        "content-type": "application/json",
    }


@respx.mock
def test_embeddings_anonymizes_input(client: TestClient) -> None:
    """The embeddings input reaches the upstream as tokens; the vector is relayed."""
    route = respx.post("https://up.example/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
    )
    response = client.post(
        "/openai/v1/embeddings",
        headers=_headers(),
        json={"model": "text-embedding-3-small", "input": "Patrick"},
    )
    assert response.status_code == 200
    assert "Patrick" not in route.calls.last.request.content.decode()
    assert response.json() == {"data": [{"embedding": [0.1, 0.2]}]}


@respx.mock
def test_completions_anonymizes_prompt_and_restores_text(client: TestClient) -> None:
    """Legacy completions anonymize the prompt and restore choices[].text."""
    respx.post("https://up.example/v1/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"text": "<<PERSON:1>> ok"}]})
    )
    response = client.post(
        "/openai/v1/completions",
        headers=_headers(),
        json={"model": "gpt-3.5-turbo-instruct", "prompt": "Patrick"},
    )
    assert response.json()["choices"][0]["text"] == "Patrick ok"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_openai_text.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Add the handlers**

In `src/piighost_api/routes/openai.py`, add to the imports from `_rewrite`:

```python
from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    anonymize_input_field,
    anonymize_prompt_field,
    deanonymize_chat_response,
    deanonymize_completion_response,
)
```

Add a small shared helper above `build_openai_router` for the "anonymize a JSON body, forward, deanonymize the reply" pattern used by completions, and a "anonymize input only, forward, relay" pattern used by embeddings/moderations:

```python
async def _proxy_json(
    request: Request,
    subpath: str,
    pipeline: ThreadAnonymizationPipeline,
    anonymize,
    deanonymize,
) -> Response:
    """Anonymize a JSON body, forward it, and optionally deanonymize the reply."""
    base = upstream_base_url(request.headers)
    headers = forward_headers(request.headers)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be JSON.")
    thread_id, ephemeral = _resolve_thread(request)
    try:
        await anonymize(body, pipeline, thread_id)
        upstream = await _forward_json(base, subpath, headers, body)
        is_json = upstream.headers.get("content-type", "").startswith("application/json")
        if is_json and upstream.status_code < 400:
            payload = upstream.json()
            if deanonymize is not None:
                await deanonymize(payload, pipeline, thread_id)
            return Response(content=payload, status_code=upstream.status_code)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )
    finally:
        if ephemeral:
            await pipeline.forget_thread(thread_id)
```

Inside `build_openai_router`, add the three handlers and register them:

```python
    @post("/completions", exclude_from_auth=True)
    async def completions(request: Request) -> Response:
        return await _proxy_json(
            request, "completions", pipeline,
            anonymize_prompt_field, deanonymize_completion_response,
        )

    @post("/embeddings", exclude_from_auth=True)
    async def embeddings(request: Request) -> Response:
        return await _proxy_json(
            request, "embeddings", pipeline, anonymize_input_field, None
        )

    @post("/moderations", exclude_from_auth=True)
    async def moderations(request: Request) -> Response:
        return await _proxy_json(
            request, "moderations", pipeline, anonymize_input_field, None
        )
```

Add `completions, embeddings, moderations` to the router's `route_handlers` list.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/routes/test_openai_text.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/openai.py tests/routes/test_openai_text.py
git commit -m "feat(proxy): completions, embeddings, moderations routes"
```

---

## Task 7: Mount the router in the app

**Files:**
- Modify: `src/piighost_api/app.py`
- Test: `tests/test_app.py` (append one integration case)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py` (which uses the shared `client` fixture with the mock pipeline):

```python
@respx.mock
def test_openai_proxy_is_mounted(client) -> None:
    """The mounted /openai proxy relays chat/completions through the app."""
    import httpx

    respx.post("https://up.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "hi"}}]}
        )
    )
    response = client.post(
        "/openai/v1/chat/completions",
        headers={
            "x-piighost-upstream": "https://up.example/v1",
            "authorization": "Bearer x",
            "content-type": "application/json",
        },
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
```

Add `import respx` at the top of `tests/test_app.py` if absent. The mock pipeline's `deanonymize` returns a fixed string, which is fine here; this test only asserts the route is reachable through the full app.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_openai_proxy_is_mounted -v`
Expected: FAIL (404, router not mounted).

- [ ] **Step 3: Mount the router**

In `src/piighost_api/app.py`, add the import near the other route imports:

```python
from piighost_api.routes.openai import build_openai_router
```

In `create_app`, build the router after the pipeline is created and add it to `route_handlers`:

```python
    openai_router = build_openai_router(pipeline)
```

and include `openai_router` in the `route_handlers=[...]` list passed to `Litestar(...)`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_app.py::test_openai_proxy_is_mounted -v`
Expected: PASS.

- [ ] **Step 5: Full suite and lint**

Run: `uv run pytest -q` (all green) and the repo's lint gate (`make lint` or `uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`).
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/piighost_api/app.py tests/test_app.py
git commit -m "feat(proxy): mount the /openai router in the app"
```

---

## Task 8: Documentation (piighost-api) and the library boundary

**Files:**
- Create: `docs/en/openai-proxy.md`, `docs/fr/openai-proxy.md` (match the repo's docs nav; add nav entries as the other pages do)
- Modify: `README.md`, `README.fr.md` (a short "OpenAI proxy" section linking the page)
- Modify (cross-repo): `~/PycharmProjects/piighost/docs/en/roadmap.md`, `~/PycharmProjects/piighost/docs/fr/roadmap.md`

- [ ] **Step 1: Write the piighost-api proxy page (EN)**

Create `docs/en/openai-proxy.md` documenting: the `/openai/v1` base path; the `X-PIIGhost-Upstream` header (required, names the OpenAI-compatible upstream); the relayed `Authorization`; the optional `X-PIIGhost-Thread-Id` (fixed thread) vs the ephemeral default; the route map (chat/completions with streaming, completions, embeddings, moderations anonymized; models/images/audio pure relay); and that unknown routes are 404. Include a curl example and an OpenAI-SDK `base_url` + `default_headers` example. Keep code blocks byte-identical to the FR page.

- [ ] **Step 2: Mirror the page (FR)**

Create `docs/fr/openai-proxy.md` with the prose translated and the code blocks byte-identical.

- [ ] **Step 3: Nav + README**

Add the page to the docs nav the way the repo declares it (mirror an existing page's nav entry in EN and FR). Add a short "OpenAI-compatible proxy" section to `README.md` and `README.fr.md` linking the page.

- [ ] **Step 4: Update the library roadmap (cross-repo)**

In `~/PycharmProjects/piighost/docs/en/roadmap.md` and `docs/fr/roadmap.md`, edit the "OpenAI-compatible proxy" section to state the boundary: the proxy is implemented in piighost-api, not the library; the library provides the building blocks (the conversation pipeline, `AsyncPlaceholderStreamDecoder`, the tool-boundary de-identification); the library is not itself an HTTP proxy. Keep EN and FR mirrored. Build both docs to confirm (`uv run zensical build --clean` and `uv run zensical build -f zensical.fr.toml`).

- [ ] **Step 5: Build piighost-api docs (if it has a docs build) and commit**

```bash
# in piighost-api
git add docs/en/openai-proxy.md docs/fr/openai-proxy.md README.md README.fr.md <nav files>
git commit -m "docs(proxy): document the OpenAI-compatible proxy (EN+FR)"
# in ~/PycharmProjects/piighost, on a docs branch or dev
git add docs/en/roadmap.md docs/fr/roadmap.md
git commit -m "docs(roadmap): clarify the OpenAI proxy lives in piighost-api"
```

---

## Self-Review

**Spec coverage:**
- `/openai/v1` router, chat/completions anonymize+deanonymize -> Task 4; streaming -> Task 5. ✓
- completions, embeddings, moderations -> Task 6. ✓
- models, images, audio pure relay + 404 on unknown -> Task 3 (unknown paths fall through to a 404 since only the listed routes are registered). ✓
- Upstream header + relayed Authorization, exclude_from_auth -> Tasks 1, 3, 4. ✓
- Ephemeral thread with forget + optional fixed thread header -> Task 4 (`_resolve_thread`, `finally` forget), Task 5 (generator forget). ✓
- Body rewrite of content + tool_call arguments; input/prompt fields -> Task 2. ✓
- Error handling (400 missing upstream / non-JSON, 502 unreachable, upstream error relayed) -> Tasks 1, 3, 4, 6. ✓
- httpx runtime dep, respx dev dep, piighost pin bump -> Task 1. ✓
- Testing offline (real ExactMatch pipeline + respx upstream) -> Tasks 2-7. ✓
- Docs (api EN+FR + README) and the library-boundary roadmap update -> Task 8. ✓

**Placeholder scan:** No stubs, TBD, or TODO. Task 5 presents `_restore_sse_chunk` directly.

**Type consistency:** `build_openai_router(pipeline) -> Router`, `_resolve_thread(request) -> tuple[str, bool]`, `_forward_json(base, subpath, headers, body)`, `_relay_raw(request, subpath)`, `_proxy_json(request, subpath, pipeline, anonymize, deanonymize)`, and the `_rewrite` function names (`anonymize_chat_request`, `deanonymize_chat_response`, `anonymize_input_field`, `anonymize_prompt_field`, `deanonymize_completion_response`) are used consistently across Tasks 2-7. The shared `_client` httpx instance is defined once in Task 3 and reused.

**Execution notes to carry:** verify the Litestar sub-path accessor (Task 3), the `AsyncPlaceholderStreamDecoder` constructor signature and `pipeline.recognizer` (Task 5), and the repo's exact lint command (Task 7) at execution time; each is flagged inline.
