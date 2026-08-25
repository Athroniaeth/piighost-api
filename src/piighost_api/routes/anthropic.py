"""Anthropic Messages-compatible proxy routes.

A transparent relay for coding-agent harnesses such as Claude Code: point
ANTHROPIC_BASE_URL at /anthropic and the harness calls /anthropic/v1/messages.
The request's text is anonymized, relayed to the real Anthropic-compatible
upstream, and the reply is deanonymized. The upstream defaults to a configured
base URL and is overridable per request with X-PIIGhost-Upstream. The caller's
x-api-key or authorization is forwarded, so these routes carry exclude_from_auth.
"""

import logging
from collections.abc import AsyncIterator

import httpx
from litestar import Request, Response, Router, post
from litestar.exceptions import HTTPException
from litestar.response import Stream

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._anthropic_shape import (
    AnthropicStreamRestorer,
    anonymize_anthropic_request,
    deanonymize_anthropic_response,
    inject_system_note,
)
from piighost_api.routes._relay import (
    _client,
    forward_json,
    relay_response_headers,
    resolve_thread,
)
from piighost_api.routes._upstream import forward_headers_permissive, upstream_base_url

logger = logging.getLogger(__name__)


def _log_upstream_error(status: int, headers: "httpx.Headers", body: bytes) -> None:
    """Log an upstream error status with its retry-after and message for diagnosis."""
    logger.warning(
        "Anthropic upstream error %s (retry-after=%s, ratelimit=%s): %s",
        status,
        headers.get("retry-after"),
        headers.get("anthropic-ratelimit-requests-remaining"),
        body[:400].decode("utf-8", "replace"),
    )


def _consume_upstream_stream(
    upstream: httpx.Response,
    pipeline: ThreadAnonymizationPipeline,
    thread_id: str,
    ephemeral: bool,
) -> AsyncIterator[bytes]:
    """Yield restored SSE bytes from an already-open upstream stream, then clean up."""

    async def replace(token: str) -> str:
        return await pipeline.deanonymize(token, thread_id)

    async def generator() -> AsyncIterator[bytes]:
        restorer = AnthropicStreamRestorer(replace)
        try:
            async for line in upstream.aiter_lines():
                restored = await restorer.feed_line(line)
                yield (restored + "\n").encode()
            trailing = restorer.flush()
            if trailing:
                yield trailing.encode()
        finally:
            await upstream.aclose()
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return generator()


def build_anthropic_router(
    pipeline: ThreadAnonymizationPipeline,
    default_upstream: str | None = None,
    anonymize_system: bool = True,
    placeholder_note: str | None = None,
) -> Router:
    """Build the /anthropic/v1 router over the given pipeline and default upstream.

    When anonymize_system is False the system prompt is relayed untouched, for a
    subscription- or enterprise-authenticated harness whose client fingerprint the
    upstream validates; message content is anonymized regardless. placeholder_note
    is opt-in guidance prepended to the system prompt, off by default: any change
    to the system prompt (this note, or anonymize_system) breaks that fingerprint
    validation on such accounts, so the upstream rejects the request. Pass a note
    only for accounts that tolerate a modified system prompt (e.g. a raw API key).
    """

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
        headers = forward_headers_permissive(request.headers)
        params = dict(request.query_params)
        body = await _read_body(request)
        thread_id, ephemeral = resolve_thread(request)
        try:
            await anonymize_anthropic_request(
                body, pipeline, thread_id, anonymize_system
            )
            if placeholder_note:
                inject_system_note(body, placeholder_note)
            if body.get("stream"):
                stream_request = _client.build_request(
                    "POST",
                    f"{base}/messages",
                    headers=headers,
                    json=body,
                    params=params,
                )
                try:
                    upstream = await _client.send(stream_request, stream=True)
                except httpx.RequestError as exc:
                    raise HTTPException(
                        status_code=502, detail=f"Upstream request failed: {exc}"
                    )
                if upstream.status_code >= 400:
                    # Do not commit to a 200 stream on an error: relay it as a
                    # normal response so the client sees the status and retry-after
                    # and backs off instead of hammering a rate limit.
                    content = await upstream.aread()
                    await upstream.aclose()
                    _log_upstream_error(upstream.status_code, upstream.headers, content)
                    return Response(
                        content=content,
                        status_code=upstream.status_code,
                        media_type=upstream.headers.get("content-type"),
                        headers=relay_response_headers(upstream.headers),
                    )
                stream = _consume_upstream_stream(
                    upstream, pipeline, thread_id, ephemeral
                )
                ephemeral = False  # the generator owns the forget now
                return Stream(stream, media_type="text/event-stream", status_code=200)
            upstream = await forward_json(base, "messages", headers, body, params)
            content_type = upstream.headers.get("content-type", "")
            if (
                content_type.startswith("application/json")
                and upstream.status_code < 400
            ):
                payload = upstream.json()
                await deanonymize_anthropic_response(payload, pipeline, thread_id)
                return Response(
                    content=payload,
                    status_code=upstream.status_code,
                    headers=relay_response_headers(upstream.headers),
                )
            if upstream.status_code >= 400:
                _log_upstream_error(
                    upstream.status_code, upstream.headers, upstream.content
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
                headers=relay_response_headers(upstream.headers),
            )
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    @post("/messages/count_tokens", exclude_from_auth=True)
    async def count_tokens(request: Request) -> Response:
        """Anonymize the request and relay it to the upstream count_tokens endpoint."""
        base = upstream_base_url(request.headers, default_upstream)
        headers = forward_headers_permissive(request.headers)
        params = dict(request.query_params)
        body = await _read_body(request)
        thread_id, ephemeral = resolve_thread(request)
        try:
            await anonymize_anthropic_request(
                body, pipeline, thread_id, anonymize_system
            )
            if placeholder_note:
                inject_system_note(body, placeholder_note)
            upstream = await forward_json(
                base, "messages/count_tokens", headers, body, params
            )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
                headers=relay_response_headers(upstream.headers),
            )
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return Router(
        path="/anthropic/v1",
        route_handlers=[messages, count_tokens],
    )
