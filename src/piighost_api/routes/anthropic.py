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
        """Parse and validate the request body as a JSON object, raising 400 otherwise."""
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
        """Anonymize the request, relay it upstream, and deanonymize the reply."""
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
        """Anonymize the request and relay it to the upstream count_tokens endpoint."""
        base = upstream_base_url(request.headers, default_upstream)
        headers = forward_headers(request.headers)
        body = await _read_body(request)
        thread_id, ephemeral = resolve_thread(request)
        try:
            await anonymize_anthropic_request(body, pipeline, thread_id)
            upstream = await forward_json(base, "messages/count_tokens", headers, body)
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
