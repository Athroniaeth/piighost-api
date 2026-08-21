"""OpenAI-compatible proxy routes.

A transparent relay: the caller points its base_url at /openai/v1 and adds an
X-PIIGhost-Upstream header naming the real OpenAI-compatible endpoint. Text routes
anonymize the request, relay it, and deanonymize the reply; metadata and
multimodal routes are byte passthroughs. The caller's Authorization is forwarded
to the upstream, so these routes carry exclude_from_auth.
"""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
from litestar import Request, Response, Router, get, post
from litestar.exceptions import HTTPException
from litestar.response import Stream

from piighost.components.placeholder import AsyncPlaceholderStreamDecoder
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    anonymize_input_field,
    anonymize_prompt_field,
    deanonymize_chat_response,
    deanonymize_completion_response,
)
from piighost_api.routes._upstream import forward_headers, upstream_base_url

_RELAY_TIMEOUT = 60.0
"""Upstream request timeout in seconds, generous enough for slow model calls."""

_ROUTER_PREFIX = "/openai/v1/"
"""Mount prefix stripped off request.url.path to recover the upstream sub-path."""

_client = httpx.AsyncClient(timeout=httpx.Timeout(_RELAY_TIMEOUT))


async def _relay_raw(request: Request, subpath: str) -> Response:
    """Forward the request to the upstream verbatim and return its raw response."""
    base = upstream_base_url(request.headers)
    headers = forward_headers(request.headers)
    body = await request.body()
    url = f"{base}/{subpath}"
    params = dict(request.query_params)
    try:
        upstream = await _client.request(
            request.method,
            url,
            headers=headers,
            content=body,
            params=params,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
    media_type = upstream.headers.get("content-type")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=media_type,
    )


def _subpath(request: Request) -> str:
    """Recover the upstream sub-path by stripping the router mount prefix."""
    return request.url.path.split(_ROUTER_PREFIX, 1)[1]


def _resolve_thread(request: Request) -> tuple[str, bool]:
    """Return (thread_id, ephemeral): a supplied fixed id, or a fresh ephemeral one."""
    supplied = request.headers.get("x-piighost-thread-id")
    if supplied:
        return supplied, False
    return uuid.uuid4().hex, True


async def _forward_json(
    base: str, subpath: str, headers: dict[str, str], body: dict
) -> httpx.Response:
    """POST a JSON body to the upstream, mapping transport errors to 502."""
    try:
        return await _client.post(f"{base}/{subpath}", headers=headers, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")


async def _restore_sse_chunk(raw: str, decoder: AsyncPlaceholderStreamDecoder) -> str:
    """Restore tokens in an SSE data line's delta content; pass other lines through."""
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
        decoder = AsyncPlaceholderStreamDecoder(replace)
        try:
            async with _client.stream(
                "POST", f"{base}/chat/completions", headers=headers, json=body
            ) as upstream:
                async for line in upstream.aiter_lines():
                    restored = await _restore_sse_chunk(line, decoder)
                    yield (restored + "\n").encode()
            trailing = decoder.flush()
            if trailing:
                yield trailing.encode()
        finally:
            if ephemeral:
                await pipeline.forget_thread(thread_id)

    return generator()


async def _proxy_json(
    request: Request,
    subpath: str,
    pipeline: ThreadAnonymizationPipeline,
    anonymize: Callable[..., Awaitable[dict]],
    deanonymize: Callable[..., Awaitable[dict]] | None,
) -> Response:
    """Anonymize a JSON body, forward it, and optionally deanonymize the reply."""
    base = upstream_base_url(request.headers)
    headers = forward_headers(request.headers)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be JSON.")
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400, detail="Request body must be a JSON object."
        )
    thread_id, ephemeral = _resolve_thread(request)
    try:
        await anonymize(body, pipeline, thread_id)
        upstream = await _forward_json(base, subpath, headers, body)
        content_type = upstream.headers.get("content-type", "")
        if content_type.startswith("application/json") and upstream.status_code < 400:
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


def build_openai_router(pipeline: ThreadAnonymizationPipeline) -> Router:
    """Build the /openai/v1 router over the given pipeline."""

    @post("/chat/completions", exclude_from_auth=True)
    async def chat_completions(request: Request) -> Response:
        base = upstream_base_url(request.headers)
        headers = forward_headers(request.headers)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body must be JSON.")
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object."
            )
        thread_id, ephemeral = _resolve_thread(request)
        try:
            await anonymize_chat_request(body, pipeline, thread_id)
            if body.get("stream"):
                stream = _stream_upstream(
                    base, headers, body, pipeline, thread_id, ephemeral
                )
                ephemeral = False  # the generator owns the forget now
                return Stream(stream, media_type="text/event-stream")
            upstream = await _forward_json(base, "chat/completions", headers, body)
            content_type = upstream.headers.get("content-type", "")
            if (
                content_type.startswith("application/json")
                and upstream.status_code < 400
            ):
                payload = upstream.json()
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

    @get(["/models", "/models/{model:str}"], exclude_from_auth=True)
    async def models(request: Request) -> Response:
        return await _relay_raw(request, _subpath(request))

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
        return await _relay_raw(request, _subpath(request))

    @post("/completions", exclude_from_auth=True)
    async def completions(request: Request) -> Response:
        return await _proxy_json(
            request,
            "completions",
            pipeline,
            anonymize_prompt_field,
            deanonymize_completion_response,
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

    return Router(
        path="/openai/v1",
        route_handlers=[
            chat_completions,
            models,
            multimodal,
            completions,
            embeddings,
            moderations,
        ],
    )
