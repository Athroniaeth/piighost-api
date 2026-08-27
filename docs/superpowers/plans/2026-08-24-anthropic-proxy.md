# Anthropic-compatible proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Anthropic Messages-compatible proxy to `piighost-api` so Claude Code can point `ANTHROPIC_BASE_URL` at it and have its PII de-identified transparently.

**Architecture:** Mirror the existing `/openai/v1` proxy. Extract the genuinely shared relay helpers, add a `default` upstream to the upstream resolver, and build a new Anthropic router over a new content-block field walker and a new SSE restorer. The anonymization core (`ThreadAnonymizationPipeline`) is reused unchanged.

**Tech Stack:** Python 3.11+, Litestar, httpx, respx (test), piighost (`ThreadAnonymizationPipeline`, `AsyncPlaceholderStreamDecoder`, `ExactMatchDetector`), pytest with `asyncio_mode="auto"`.

**Spec:** `docs/superpowers/specs/2026-08-24-anthropic-proxy-design.md`

**Test command:** `uv run pytest <path> -v` from the repo root. The changes are all in `piighost-api`, so the PyPI-pinned `piighost` is sufficient; no `make dev-local` is needed for the suite.

---

## File structure

- `src/piighost_api/routes/_relay.py` (create): shared outbound-relay helpers moved out of `openai.py` (`_client`, `_RELAY_TIMEOUT`, `_forward_json`, `_resolve_thread`), so both provider routers share one httpx client and one thread-resolution rule.
- `src/piighost_api/routes/_upstream.py` (modify): `upstream_base_url(headers, default=None)` falls back to a configured default; `_FORWARDED` gains the Anthropic auth/version headers.
- `src/piighost_api/routes/openai.py` (modify): import the relay helpers from `_relay.py`; thread a `default_upstream` through `build_openai_router` and its handlers.
- `src/piighost_api/routes/_anthropic_shape.py` (create): the Anthropic content-block field walker (request anonymize, response deanonymize) and the `AnthropicStreamRestorer`.
- `src/piighost_api/routes/anthropic.py` (create): `build_anthropic_router(pipeline, default_upstream)` with `POST /messages` (non-stream and stream) and `POST /messages/count_tokens`.
- `src/piighost_api/app.py` (modify): read `PIIGHOST_ANTHROPIC_UPSTREAM` / `PIIGHOST_OPENAI_UPSTREAM`, build and register the Anthropic router.
- `tests/routes/test_upstream.py` (modify): default-fallback and Anthropic-header-forwarding unit tests.
- `tests/routes/test_anthropic_shape.py` (create): field-walker and stream-restorer unit tests.
- `tests/routes/test_anthropic_messages.py` (create): respx integration tests for the router.
- `tests/routes/test_anthropic_app.py` (create): the app registers the Anthropic route.
- `docs/en/anthropic-proxy.md` (create): the real-run procedure with Claude Code.

---

## Task 1: Extract shared relay helpers into `_relay.py`

Pure refactor. `_client`, `_RELAY_TIMEOUT`, `_forward_json`, and `_resolve_thread` currently live in `openai.py`; the Anthropic router needs the same ones. Move them to `_relay.py` and re-import in `openai.py`. Existing OpenAI proxy tests are the safety net.

**Files:**
- Create: `src/piighost_api/routes/_relay.py`
- Modify: `src/piighost_api/routes/openai.py`

- [ ] **Step 1: Create `_relay.py` with the moved helpers**

Create `src/piighost_api/routes/_relay.py`:

```python
"""Shared outbound-relay helpers for the provider proxies.

One httpx client and one thread-resolution rule are shared by the OpenAI and
Anthropic routers, so both relay to their upstream the same way.
"""

import uuid

import httpx
from litestar import Request
from litestar.exceptions import HTTPException

_RELAY_TIMEOUT = 60.0
"""Upstream request timeout in seconds, generous enough for slow model calls."""

_client = httpx.AsyncClient(timeout=httpx.Timeout(_RELAY_TIMEOUT))


def resolve_thread(request: Request) -> tuple[str, bool]:
    """Return (thread_id, ephemeral): a supplied fixed id, or a fresh ephemeral one."""
    supplied = request.headers.get("x-piighost-thread-id")
    if supplied:
        return supplied, False
    return uuid.uuid4().hex, True


async def forward_json(
    base: str, subpath: str, headers: dict[str, str], body: dict
) -> httpx.Response:
    """POST a JSON body to the upstream, mapping transport errors to 502."""
    try:
        return await _client.post(f"{base}/{subpath}", headers=headers, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
```

- [ ] **Step 2: Rewrite `openai.py` to import from `_relay.py`**

In `src/piighost_api/routes/openai.py`, remove the local `import uuid`, the `_RELAY_TIMEOUT`, `_client`, `_resolve_thread`, and `_forward_json` definitions, and import them instead. Change the import block near the top (currently lines 10-29) so it reads:

```python
import json
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
from litestar import Request, Response, Router, get, post
from litestar.exceptions import HTTPException
from litestar.response import Stream

from piighost.components.placeholder import AsyncPlaceholderStreamDecoder
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._relay import _client, forward_json, resolve_thread
from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    anonymize_input_field,
    anonymize_prompt_field,
    deanonymize_chat_response,
    deanonymize_completion_response,
)
from piighost_api.routes._upstream import forward_headers, upstream_base_url
```

Delete the old module-level `_RELAY_TIMEOUT = ...` and `_client = httpx.AsyncClient(...)` lines. Keep `_ROUTER_PREFIX`. Delete the `_resolve_thread` and `_forward_json` function definitions. Then update the three call sites: replace `_resolve_thread(request)` with `resolve_thread(request)` and `_forward_json(` with `forward_json(` (both occurrences). `_client` keeps its name via the import, so `_relay_raw` and `_stream_upstream` need no edit beyond the import.

- [ ] **Step 3: Run the OpenAI proxy suite to prove the refactor is green**

Run: `uv run pytest tests/routes/test_openai_chat.py tests/routes/test_openai_stream.py tests/routes/test_openai_relay.py tests/routes/test_openai_text.py -v`
Expected: PASS (same set as before the move).

- [ ] **Step 4: Commit**

```bash
git add src/piighost_api/routes/_relay.py src/piighost_api/routes/openai.py
git commit -m "refactor(routes): extract shared relay helpers into _relay"
```

---

## Task 2: Add a default upstream and Anthropic headers to `_upstream.py`

`upstream_base_url` currently raises 400 when the header is absent. Give it a `default` so a server-configured upstream is used when Claude Code sends no header, with the header still winning. Extend `forward_headers` so Anthropic's `x-api-key`, `anthropic-version`, and `anthropic-beta` are relayed when present.

**Files:**
- Modify: `src/piighost_api/routes/_upstream.py`
- Test: `tests/routes/test_upstream.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/routes/test_upstream.py`:

```python
def test_upstream_default_used_when_header_absent() -> None:
    """With no X-PIIGhost-Upstream header, the configured default is returned."""
    headers = {"authorization": "Bearer x"}
    assert (
        upstream_base_url(headers, default="https://api.anthropic.com/v1")
        == "https://api.anthropic.com/v1"
    )


def test_upstream_header_wins_over_default() -> None:
    """A present header overrides the configured default."""
    headers = {"x-piighost-upstream": "https://custom/v1"}
    assert (
        upstream_base_url(headers, default="https://api.anthropic.com/v1")
        == "https://custom/v1"
    )


def test_upstream_no_header_no_default_is_400() -> None:
    """With neither a header nor a default, resolution is a 400."""
    with pytest.raises(HTTPException) as exc:
        upstream_base_url({}, default=None)
    assert exc.value.status_code == 400


def test_forward_headers_relays_anthropic_headers() -> None:
    """Anthropic auth and version headers are forwarded when present."""
    headers = {
        "x-api-key": "sk-ant",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "tools-2024",
        "x-piighost-upstream": "https://custom/v1",
    }
    forwarded = forward_headers(headers)
    assert forwarded["x-api-key"] == "sk-ant"
    assert forwarded["anthropic-version"] == "2023-06-01"
    assert forwarded["anthropic-beta"] == "tools-2024"
    assert "x-piighost-upstream" not in forwarded
```

Check the top of `tests/routes/test_upstream.py`. If `pytest`, `HTTPException`, `upstream_base_url`, or `forward_headers` are not already imported there, add:

```python
import pytest
from litestar.exceptions import HTTPException

from piighost_api.routes._upstream import forward_headers, upstream_base_url
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/routes/test_upstream.py -v`
Expected: FAIL. `test_upstream_default_used_when_header_absent` fails with `TypeError: upstream_base_url() got an unexpected keyword argument 'default'`; the header-forwarding test fails on the missing keys.

- [ ] **Step 3: Implement the changes**

In `src/piighost_api/routes/_upstream.py`, change `_FORWARDED` and `upstream_base_url`:

```python
_FORWARDED = (
    "authorization",
    "content-type",
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
)


def upstream_base_url(headers: _HeaderMap, default: str | None = None) -> str:
    """Return the upstream base URL: the header if set, else the default.

    Raises 400 only when neither a header nor a default is available.
    """
    raw = headers.get(UPSTREAM_HEADER)
    if raw:
        return raw.rstrip("/")
    if default:
        return default.rstrip("/")
    raise HTTPException(
        status_code=400,
        detail=(
            f"Missing {UPSTREAM_HEADER} header and no default upstream is "
            "configured. Set it to a provider base URL, e.g. "
            "https://api.openai.com/v1 or https://api.anthropic.com/v1."
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/routes/test_upstream.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/_upstream.py tests/routes/test_upstream.py
git commit -m "feat(routes): default upstream fallback and Anthropic header forwarding"
```

---

## Task 3: Anthropic request anonymization walker

Create the field walker that anonymizes `system`, `messages[].content`, and the content blocks (`text`, `tool_use.input`, `tool_result.content`), leaving `image`/`document` and `tools[]` untouched. Reuse `_anonymizer`, `_deanonymizer`, and `_map_strings` from `_rewrite.py`.

**Files:**
- Create: `src/piighost_api/routes/_anthropic_shape.py`
- Test: `tests/routes/test_anthropic_shape.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/routes/test_anthropic_shape.py`:

```python
"""Unit tests for the Anthropic content-block walker and stream restorer."""

import pytest

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._anthropic_shape import anonymize_anthropic_request


@pytest.fixture
def pipeline() -> ThreadAnonymizationPipeline:
    return ThreadAnonymizationPipeline(ExactMatchDetector({"Patrick": "PERSON"}))


async def test_anonymize_string_system_and_message(pipeline) -> None:
    body = {
        "system": "You help Patrick.",
        "messages": [{"role": "user", "content": "I am Patrick"}],
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert "Patrick" not in body["system"]
    assert "<<PERSON:1>>" in body["system"]
    assert body["messages"][0]["content"] == "<<PERSON:1>>"


async def test_anonymize_block_system_and_text_block(pipeline) -> None:
    body = {
        "system": [{"type": "text", "text": "Help Patrick."}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "I am Patrick"}]}
        ],
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert "Patrick" not in body["system"][0]["text"]
    assert body["messages"][0]["content"][0]["text"] == "<<PERSON:1>>"


async def test_anonymize_tool_use_input_and_tool_result(pipeline) -> None:
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "run",
                        "input": {"cmd": "echo Patrick"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "Patrick was here",
                    }
                ],
            },
        ]
    }
    await anonymize_anthropic_request(body, pipeline, "t1")
    tool_use = body["messages"][0]["content"][0]
    tool_result = body["messages"][1]["content"][0]
    assert tool_use["input"]["cmd"] == "echo <<PERSON:1>>"
    assert tool_result["content"] == "<<PERSON:1>> was here"


async def test_image_block_is_passthrough(pipeline) -> None:
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    body = {"messages": [{"role": "user", "content": [image]}]}
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert body["messages"][0]["content"][0] == image


async def test_tools_definitions_are_passthrough(pipeline) -> None:
    tools = [{"name": "run", "description": "Patrick's tool", "input_schema": {}}]
    body = {"tools": tools, "messages": [{"role": "user", "content": "hi"}]}
    await anonymize_anthropic_request(body, pipeline, "t1")
    assert body["tools"] == tools
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/routes/test_anthropic_shape.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'piighost_api.routes._anthropic_shape'`.

- [ ] **Step 3: Implement the walker**

Create `src/piighost_api/routes/_anthropic_shape.py`:

```python
"""Field-level anonymize and deanonymize for Anthropic Messages bodies.

Only known text-bearing fields are rewritten: the system prompt, message
content blocks, tool_use inputs, and tool_result contents. Images, documents,
and tool definitions are forwarded untouched, so the proxy stays robust to the
Anthropic schema evolving. All rewriting goes through the pipeline's public
anonymize and deanonymize, over a single thread per request.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import _anonymizer, _deanonymizer, _map_strings

_StringOp = Callable[[str], Awaitable[str]]


async def _rewrite_block(block: Any, op: _StringOp) -> Any:
    """Rewrite one content block by type; pass unknown or binary blocks through."""
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return {**block, "text": await op(block["text"])}
    if block_type == "tool_use" and isinstance(block.get("input"), (dict, list)):
        return {**block, "input": await _map_strings(block["input"], op)}
    if block_type == "tool_result" and "content" in block:
        return {**block, "content": await _rewrite_content(block["content"], op)}
    return block


async def _rewrite_content(content: Any, op: _StringOp) -> Any:
    """Rewrite a message content: a string, or a list of content blocks."""
    if isinstance(content, str):
        return await op(content)
    if isinstance(content, list):
        return [await _rewrite_block(block, op) for block in content]
    return content


async def _rewrite_system(system: Any, op: _StringOp) -> Any:
    """Rewrite the system prompt: a string, or a list of text blocks."""
    if isinstance(system, str):
        return await op(system)
    if isinstance(system, list):
        rewritten = []
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block = {**block, "text": await op(block["text"])}
            rewritten.append(block)
        return rewritten
    return system


async def _rewrite_messages(messages: Any, op: _StringOp) -> None:
    """Rewrite each message's content in place."""
    if not isinstance(messages, list):
        return
    for message in messages:
        if isinstance(message, dict) and "content" in message:
            message["content"] = await _rewrite_content(message["content"], op)


async def anonymize_anthropic_request(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Anonymize a Messages request body's system prompt and message content."""
    op = _anonymizer(pipeline, thread_id)
    if "system" in body:
        body["system"] = await _rewrite_system(body["system"], op)
    await _rewrite_messages(body.get("messages"), op)
    return body


async def deanonymize_anthropic_response(
    body: dict[str, Any], pipeline: ThreadAnonymizationPipeline, thread_id: str
) -> dict[str, Any]:
    """Deanonymize a Messages response body's content blocks."""
    op = _deanonymizer(pipeline, thread_id)
    if isinstance(body.get("content"), list):
        body["content"] = [await _rewrite_block(block, op) for block in body["content"]]
    return body
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/routes/test_anthropic_shape.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/_anthropic_shape.py tests/routes/test_anthropic_shape.py
git commit -m "feat(routes): Anthropic request/response content-block walker"
```

---

## Task 4: Response deanonymization test

`deanonymize_anthropic_response` was written in Task 3; lock its behavior with tests, including token restoration inside a `tool_use.input`.

**Files:**
- Test: `tests/routes/test_anthropic_shape.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/routes/test_anthropic_shape.py`:

```python
from piighost_api.routes._anthropic_shape import deanonymize_anthropic_response


async def _prime(pipeline: ThreadAnonymizationPipeline, thread_id: str) -> None:
    """Anonymize 'Patrick' so <<PERSON:1>> maps back to it in the thread."""
    await pipeline.anonymize("Patrick", thread_id)


async def test_deanonymize_text_block(pipeline) -> None:
    await _prime(pipeline, "t1")
    body = {"content": [{"type": "text", "text": "Hi <<PERSON:1>>"}]}
    await deanonymize_anthropic_response(body, pipeline, "t1")
    assert body["content"][0]["text"] == "Hi Patrick"


async def test_deanonymize_tool_use_input(pipeline) -> None:
    await _prime(pipeline, "t1")
    body = {
        "content": [
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "run",
                "input": {"cmd": "echo <<PERSON:1>>"},
            }
        ]
    }
    await deanonymize_anthropic_response(body, pipeline, "t1")
    assert body["content"][0]["input"]["cmd"] == "echo Patrick"
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `uv run pytest tests/routes/test_anthropic_shape.py -k deanonymize -v`
Expected: PASS (2 tests). They pass immediately because the implementation from Task 3 already covers them; this task pins the response contract with explicit tests.

- [ ] **Step 3: Commit**

```bash
git add tests/routes/test_anthropic_shape.py
git commit -m "test(routes): pin Anthropic response deanonymization"
```

---

## Task 5: The SSE stream restorer

Add `AnthropicStreamRestorer`: a stateful, per-content-block-index restorer that rewrites `text_delta.text` and `input_json_delta.partial_json` inside `content_block_delta` events, passing every other line through.

**Files:**
- Modify: `src/piighost_api/routes/_anthropic_shape.py`
- Test: `tests/routes/test_anthropic_shape.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/routes/test_anthropic_shape.py`:

```python
import json

from piighost_api.routes._anthropic_shape import AnthropicStreamRestorer


def _restorer(pipeline: ThreadAnonymizationPipeline, thread_id: str):
    async def replace(token: str) -> str:
        return await pipeline.deanonymize(token, thread_id)

    return AnthropicStreamRestorer(replace)


async def test_restorer_passes_event_lines_through(pipeline) -> None:
    restorer = _restorer(pipeline, "t1")
    assert await restorer.feed_line("event: content_block_delta") == (
        "event: content_block_delta"
    )
    assert await restorer.feed_line("") == ""


async def test_restorer_restores_text_split_across_deltas(pipeline) -> None:
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    first = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"Hi <<PER"}}'
    )
    second = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"SON:1>>"}}'
    )
    out_first = await restorer.feed_line(first)
    out_second = await restorer.feed_line(second)
    combined = json.loads(out_first[len("data: ") :])["delta"]["text"] + json.loads(
        out_second[len("data: ") :]
    )["delta"]["text"]
    assert combined == "Hi Patrick"
    assert "<<PERSON" not in combined


async def test_restorer_restores_tool_input_json_safely(pipeline) -> None:
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    line = (
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"cmd\\": \\"echo <<PERSON:1>>\\"}"}}'
    )
    out = await restorer.feed_line(line)
    event = json.loads(out[len("data: ") :])
    assert event["delta"]["partial_json"] == '{"cmd": "echo Patrick"}'


async def test_restorer_per_index_decoders_do_not_bleed(pipeline) -> None:
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    # Index 0 holds an open fragment; index 1 must not consume it.
    open_frag = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"<<PER"}}'
    )
    other = (
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"clean"}}'
    )
    await restorer.feed_line(open_frag)
    out_other = await restorer.feed_line(other)
    assert json.loads(out_other[len("data: ") :])["delta"]["text"] == "clean"


async def test_restorer_flush_emits_trailing_fragment(pipeline) -> None:
    await _prime(pipeline, "t1")
    restorer = _restorer(pipeline, "t1")
    line = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"end <<PER"}}'
    )
    await restorer.feed_line(line)
    assert restorer.flush() == "<<PER"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/routes/test_anthropic_shape.py -k restorer -v`
Expected: FAIL with `ImportError: cannot import name 'AnthropicStreamRestorer'`.

- [ ] **Step 3: Implement the restorer**

Add to the top of `src/piighost_api/routes/_anthropic_shape.py` imports:

```python
import json

from piighost.components.placeholder import AsyncPlaceholderStreamDecoder
```

Append to `src/piighost_api/routes/_anthropic_shape.py`:

```python
class AnthropicStreamRestorer:
    """Restore tokens in an Anthropic SSE stream, per content-block index.

    Anthropic streams typed events; only content_block_delta carries model text.
    Each block index gets its own AsyncPlaceholderStreamDecoder so a token split
    across deltas is reassembled without one block bleeding a held fragment into
    another. text_delta.text and input_json_delta.partial_json are restored the
    same way: we rewrite the decoded string value and re-serialize the event, so
    JSON escaping of the restored value is automatic. Every other line, including
    the event: lines and blank separators, passes through unchanged.
    """

    def __init__(self, replace: _StringOp) -> None:
        self._replace = replace
        self._decoders: dict[int, AsyncPlaceholderStreamDecoder] = {}

    def _decoder(self, index: int) -> AsyncPlaceholderStreamDecoder:
        decoder = self._decoders.get(index)
        if decoder is None:
            decoder = AsyncPlaceholderStreamDecoder(self._replace)
            self._decoders[index] = decoder
        return decoder

    async def feed_line(self, raw: str) -> str:
        """Restore tokens in a data line's delta; return other lines unchanged."""
        if not raw.startswith("data:"):
            return raw
        payload = raw[len("data:") :].strip()
        if not payload:
            return raw
        try:
            event = json.loads(payload)
        except ValueError:
            return raw
        if event.get("type") != "content_block_delta":
            return raw
        index = event.get("index", 0)
        delta = event.get("delta")
        if isinstance(delta, dict):
            decoder = self._decoder(index)
            if isinstance(delta.get("text"), str):
                delta["text"] = await decoder.feed(delta["text"])
            elif isinstance(delta.get("partial_json"), str):
                delta["partial_json"] = await decoder.feed(delta["partial_json"])
        return "data: " + json.dumps(event)

    def flush(self) -> str:
        """Emit any trailing fragments the decoders still hold (truncated stream)."""
        return "".join(decoder.flush() for decoder in self._decoders.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/routes/test_anthropic_shape.py -k restorer -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/_anthropic_shape.py tests/routes/test_anthropic_shape.py
git commit -m "feat(routes): Anthropic SSE per-index stream restorer"
```

---

## Task 6: The Anthropic router

Build `build_anthropic_router(pipeline, default_upstream)` with `POST /messages` (non-stream and stream) and `POST /messages/count_tokens`, mounted at `/anthropic/v1`. Mirror `chat_completions` from `openai.py`.

**Files:**
- Create: `src/piighost_api/routes/anthropic.py`
- Test: `tests/routes/test_anthropic_messages.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/routes/test_anthropic_messages.py`:

```python
"""Integration tests for the Anthropic-proxy messages route."""

import httpx
import pytest
import respx
from litestar import Litestar
from litestar.testing import TestClient

from piighost.components.detector import ExactMatchDetector
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes.anthropic import build_anthropic_router

_DEFAULT = "https://api.anthropic.com/v1"


@pytest.fixture
def client() -> TestClient:
    detector = ExactMatchDetector({"Patrick": "PERSON"})
    pipeline = ThreadAnonymizationPipeline(detector)
    router = build_anthropic_router(pipeline, default_upstream=_DEFAULT)
    app = Litestar(route_handlers=[router])
    return TestClient(app=app)


_HEADERS = {
    "x-api-key": "sk-ant",
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/routes/test_anthropic_messages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'piighost_api.routes.anthropic'`.

- [ ] **Step 3: Implement the router**

Create `src/piighost_api/routes/anthropic.py`:

```python
"""Anthropic Messages-compatible proxy routes.

A transparent relay for coding-agent harnesses such as Claude Code: point
ANTHROPIC_BASE_URL at /anthropic and the harness calls /anthropic/v1/messages.
The request's text is anonymized, relayed to the real Anthropic-compatible
upstream, and the reply is deanonymized. The upstream defaults to a configured
base URL and is overridable per request with X-PIIGhost-Upstream. The caller's
x-api-key or authorization is forwarded, so these routes carry exclude_from_auth.
"""

from collections.abc import AsyncIterator

from litestar import Request, Response, Router, post
from litestar.exceptions import HTTPException
from litestar.response import Stream

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._anthropic_shape import (
    AnthropicStreamRestorer,
    anonymize_anthropic_request,
    deanonymize_anthropic_response,
)
from piighost_api.routes._relay import _client, forward_json, resolve_thread
from piighost_api.routes._upstream import forward_headers, upstream_base_url


def _stream_upstream(
    base: str,
    headers: dict[str, str],
    body: dict,
    pipeline: ThreadAnonymizationPipeline,
    thread_id: str,
    ephemeral: bool,
) -> AsyncIterator[bytes]:
    """Yield restored SSE bytes from the upstream stream, then forget if ephemeral."""

    async def replace(token: str) -> str:
        return await pipeline.deanonymize(token, thread_id)

    async def generator() -> AsyncIterator[bytes]:
        restorer = AnthropicStreamRestorer(replace)
        try:
            async with _client.stream(
                "POST", f"{base}/messages", headers=headers, json=body
            ) as upstream:
                async for line in upstream.aiter_lines():
                    restored = await restorer.feed_line(line)
                    yield (restored + "\n").encode()
            trailing = restorer.flush()
            if trailing:
                yield trailing.encode()
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return generator()


def build_anthropic_router(
    pipeline: ThreadAnonymizationPipeline, default_upstream: str | None = None
) -> Router:
    """Build the /anthropic/v1 router over the given pipeline and default upstream."""

    async def _read_body(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body must be JSON.")
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object."
            )
        return body

    @post("/messages", exclude_from_auth=True)
    async def messages(request: Request) -> Response:
        base = upstream_base_url(request.headers, default_upstream)
        headers = forward_headers(request.headers)
        body = await _read_body(request)
        thread_id, ephemeral = resolve_thread(request)
        try:
            await anonymize_anthropic_request(body, pipeline, thread_id)
            if body.get("stream"):
                stream = _stream_upstream(
                    base, headers, body, pipeline, thread_id, ephemeral
                )
                ephemeral = False  # the generator owns the forget now
                return Stream(stream, media_type="text/event-stream")
            upstream = await forward_json(base, "messages", headers, body)
            content_type = upstream.headers.get("content-type", "")
            if (
                content_type.startswith("application/json")
                and upstream.status_code < 400
            ):
                payload = upstream.json()
                await deanonymize_anthropic_response(payload, pipeline, thread_id)
                return Response(content=payload, status_code=upstream.status_code)
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
            )
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    @post("/messages/count_tokens", exclude_from_auth=True)
    async def count_tokens(request: Request) -> Response:
        base = upstream_base_url(request.headers, default_upstream)
        headers = forward_headers(request.headers)
        body = await _read_body(request)
        thread_id, ephemeral = resolve_thread(request)
        try:
            await anonymize_anthropic_request(body, pipeline, thread_id)
            upstream = await forward_json(
                base, "messages/count_tokens", headers, body
            )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
            )
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return Router(
        path="/anthropic/v1",
        route_handlers=[messages, count_tokens],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/routes/test_anthropic_messages.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/routes/anthropic.py tests/routes/test_anthropic_messages.py
git commit -m "feat(routes): Anthropic Messages proxy router"
```

---

## Task 7: Wire the Anthropic router into the app

Register the router in `create_app`, reading its default upstream from the environment, and give the OpenAI router its own configurable default too.

**Files:**
- Modify: `src/piighost_api/app.py`
- Test: `tests/routes/test_anthropic_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/routes/test_anthropic_app.py`:

```python
"""The app factory registers the Anthropic proxy route."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from litestar import Litestar

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _mock_pipeline() -> MagicMock:
    pipeline = MagicMock()
    pipeline.anonymize = AsyncMock()
    pipeline.deanonymize = AsyncMock()
    pipeline.forget_thread = AsyncMock()
    pipeline.detector = MagicMock()
    return pipeline


def test_app_registers_anthropic_messages_route() -> None:
    config = MagicMock()
    config.name = "test"
    config.detector.type = "exact"
    with patch("piighost_api.app.load_config", return_value=config):
        with patch(
            "piighost_api.app.load_thread_pipeline", return_value=_mock_pipeline()
        ):
            from piighost_api.app import create_app

            app: Litestar = create_app(FIXTURES / "minimal.toml")
    paths = {route.path for route in app.routes}
    assert "/anthropic/v1/messages" in paths
    assert "/anthropic/v1/messages/count_tokens" in paths
```

If `tests/fixtures/minimal.toml` does not exist, check how `tests/conftest.py` references `FIXTURES / "minimal.toml"` and reuse that same fixture path; the conftest already relies on it, so it exists. Keep this test's `FIXTURES` pointing at the same directory.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/routes/test_anthropic_app.py -v`
Expected: FAIL on the assertion; `/anthropic/v1/messages` is not among the registered paths.

- [ ] **Step 3: Implement the wiring**

In `src/piighost_api/app.py`, add the import next to the OpenAI one (currently line 41):

```python
from piighost_api.routes.anthropic import build_anthropic_router
from piighost_api.routes.openai import build_openai_router
```

In `create_app`, replace the router construction (currently line 248) with:

```python
    openai_upstream = os.getenv("PIIGHOST_OPENAI_UPSTREAM", "https://api.openai.com/v1")
    anthropic_upstream = os.getenv(
        "PIIGHOST_ANTHROPIC_UPSTREAM", "https://api.anthropic.com/v1"
    )
    openai_router = build_openai_router(pipeline, openai_upstream)
    anthropic_router = build_anthropic_router(pipeline, anthropic_upstream)
```

Add `anthropic_router` to the `route_handlers` list in the `Litestar(...)` call, right after `openai_router` (currently line 439):

```python
            openai_router,
            anthropic_router,
```

- [ ] **Step 4: Update `build_openai_router` to accept the default upstream**

`build_openai_router` and its handlers currently call `upstream_base_url(request.headers)` with no default. In `src/piighost_api/routes/openai.py`, change the signature and thread the default through.

Change the signature:

```python
def build_openai_router(
    pipeline: ThreadAnonymizationPipeline, default_upstream: str | None = None
) -> Router:
```

In `chat_completions`, change the first line of its body:

```python
        base = upstream_base_url(request.headers, default_upstream)
```

`_relay_raw` and `_proxy_json` are module-level and call `upstream_base_url(request.headers)`. Give each a `default` parameter and pass it from the handlers. Change their signatures and first resolution line:

```python
async def _relay_raw(request: Request, subpath: str, default: str | None) -> Response:
    """Forward the request to the upstream verbatim and return its raw response."""
    base = upstream_base_url(request.headers, default)
    ...
```

```python
async def _proxy_json(
    request: Request,
    subpath: str,
    pipeline: ThreadAnonymizationPipeline,
    anonymize: Callable[..., Awaitable[dict]],
    deanonymize: Callable[..., Awaitable[dict]] | None,
    default: str | None,
) -> Response:
    """Anonymize a JSON body, forward it, and optionally deanonymize the reply."""
    base = upstream_base_url(request.headers, default)
    ...
```

Update the handler call sites inside `build_openai_router`:

```python
    @get(["/models", "/models/{model:str}"], exclude_from_auth=True)
    async def models(request: Request) -> Response:
        return await _relay_raw(request, _subpath(request), default_upstream)

    @post([...], exclude_from_auth=True)
    async def multimodal(request: Request) -> Response:
        return await _relay_raw(request, _subpath(request), default_upstream)

    @post("/completions", exclude_from_auth=True)
    async def completions(request: Request) -> Response:
        return await _proxy_json(
            request,
            "completions",
            pipeline,
            anonymize_prompt_field,
            deanonymize_completion_response,
            default_upstream,
        )

    @post("/embeddings", exclude_from_auth=True)
    async def embeddings(request: Request) -> Response:
        return await _proxy_json(
            request, "embeddings", pipeline, anonymize_input_field, None, default_upstream
        )

    @post("/moderations", exclude_from_auth=True)
    async def moderations(request: Request) -> Response:
        return await _proxy_json(
            request, "moderations", pipeline, anonymize_input_field, None, default_upstream
        )
```

- [ ] **Step 5: Run the app and OpenAI tests to verify all green**

Run: `uv run pytest tests/routes/test_anthropic_app.py tests/routes/test_openai_chat.py tests/routes/test_openai_relay.py tests/routes/test_openai_text.py tests/routes/test_openai_stream.py -v`
Expected: PASS. The OpenAI tests still send `x-piighost-upstream`, so the header wins over the new default and their behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/piighost_api/app.py src/piighost_api/routes/openai.py tests/routes/test_anthropic_app.py
git commit -m "feat(app): register the Anthropic proxy and configurable upstream defaults"
```

---

## Task 8: Full suite, lint, and the real-run doc

Run the whole suite and the lint gate, then document the manual Claude Code run so the "real situation" validation is reproducible.

**Files:**
- Create: `docs/en/anthropic-proxy.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest`
Expected: PASS, including the pre-existing suite plus the new `test_upstream.py`, `test_anthropic_shape.py`, `test_anthropic_messages.py`, and `test_anthropic_app.py`.

- [ ] **Step 2: Run the lint gate**

Run: `make lint`
Expected: PASS. If `make lint` reports formatting, run `make format` and re-run `make lint`. Fix any pyrefly type complaint (for example, annotate a `default: str | None` parameter that a caller passes positionally).

- [ ] **Step 3: Write the real-run doc**

Create `docs/en/anthropic-proxy.md`:

```markdown
# Running Claude Code through the proxy

The Anthropic proxy lets Claude Code speak to a real Anthropic-compatible
upstream while piighost de-identifies every request and restores every reply.
Claude Code never sends the real PII to the model.

## The classic proof

1. Start the API with a pipeline whose detector matches your own first name.
   The simplest reproducible setup is an `ExactMatchDetector`, configured in a
   TOML file:

       [detector]
       type = "exact"

       [detector.values]
       Patrick = "PERSON"

   Run the server against that config on a local port, for example 8080.

2. Point Claude Code at the proxy and give it a real key:

       export ANTHROPIC_BASE_URL=http://localhost:8080/anthropic
       export ANTHROPIC_API_KEY=sk-ant-...
       claude

3. In Claude Code, ask for the first letter of that first name, for example
   "What is the first letter of my name, Patrick?".

4. Observe two things. Claude cannot give the letter, because it only ever saw
   `<<PERSON:1>>`, not `Patrick`. Meanwhile the transcript you read is restored,
   so you still see `Patrick`. The model was blind to the PII the whole time.

## Notes

- The upstream defaults to `https://api.anthropic.com/v1`. Override it per
  request with an `X-PIIGhost-Upstream` header, or globally with the
  `PIIGHOST_ANTHROPIC_UPSTREAM` environment variable, to target a gateway.
- Prefer an API key over subscription OAuth. OAuth behaves poorly through a
  custom base URL.
- Each request is a fresh ephemeral thread, matching how Claude Code resends the
  whole history every turn. Pass `X-PIIGhost-Thread-ID` only if you want a
  persistent thread you manage yourself.
```

Then mirror it to `docs/fr/anthropic-proxy.md` if this repo keeps bilingual docs (check whether `docs/fr/` exists; the piighost library mirrors EN and FR, but confirm the same applies here before duplicating).

- [ ] **Step 4: Commit**

```bash
git add docs/en/anthropic-proxy.md
git commit -m "docs: how to run Claude Code through the Anthropic proxy"
```

---

## Self-review notes

- **Spec coverage:** upstream default + header override (Task 2, Task 6), header forwarding (Task 2, Task 6), system/text/tool_use/tool_result anonymize (Task 3), image/document + tools passthrough (Task 3), response deanonymize incl. tool_use input (Task 4), SSE per-index restore for text and tool args (Task 5, Task 6), count_tokens (Task 6), ephemeral thread (inherited from `resolve_thread`, exercised by Task 6), app wiring (Task 7), mocked suite + real-run doc (Task 6, Task 8). All spec sections map to a task.
- **Type consistency:** `resolve_thread`/`forward_json` (public, in `_relay.py`) are used by both routers; `_anonymizer`/`_deanonymizer`/`_map_strings` (imported from `_rewrite.py`) keep their names; `anonymize_anthropic_request`/`deanonymize_anthropic_response`/`AnthropicStreamRestorer`/`build_anthropic_router` are referenced consistently across Tasks 3-7.
- **No placeholders:** every code step shows the full code; every run step gives the exact command and expected result.
