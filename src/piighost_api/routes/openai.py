"""OpenAI-compatible proxy routes.

A transparent relay: the caller points its base_url at /openai/v1 and adds an
X-PIIGhost-Upstream header naming the real OpenAI-compatible endpoint. Text routes
anonymize the request, relay it, and deanonymize the reply; metadata and
multimodal routes are byte passthroughs. The caller's Authorization is forwarded
to the upstream, so these routes carry exclude_from_auth.
"""

import httpx
from litestar import Request, Response, Router, get, post
from litestar.exceptions import HTTPException

from piighost.pipeline import ThreadAnonymizationPipeline

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


def build_openai_router(pipeline: ThreadAnonymizationPipeline) -> Router:
    """Build the /openai/v1 router over the given pipeline."""

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

    return Router(path="/openai/v1", route_handlers=[models, multimodal])
