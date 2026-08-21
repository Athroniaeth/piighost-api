"""OpenAI-compatible proxy routes.

A transparent relay: the caller points its base_url at /openai/v1 and adds an
X-PIIGhost-Upstream header naming the real OpenAI-compatible endpoint. Text routes
anonymize the request, relay it, and deanonymize the reply; metadata and
multimodal routes are byte passthroughs. The caller's Authorization is forwarded
to the upstream, so these routes carry exclude_from_auth.
"""

import uuid

import httpx
from litestar import Request, Response, Router, get, post
from litestar.exceptions import HTTPException

from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.routes._rewrite import (
    anonymize_chat_request,
    deanonymize_chat_response,
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
            if body.get("stream"):
                raise HTTPException(
                    status_code=400,
                    detail="Streaming is handled by the streaming path.",
                )
            await anonymize_chat_request(body, pipeline, thread_id)
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

    return Router(
        path="/openai/v1",
        route_handlers=[chat_completions, models, multimodal],
    )
