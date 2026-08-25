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
"""Shared async HTTP client for all provider proxy routers."""


_DROPPED_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "content-type",
    }
)
"""Upstream response headers the proxy must not relay: the length and encoding
ones this server recomputes for the rewritten body, hop-by-hop, and content-type
which each route sets from its own media type."""


def relay_response_headers(headers: "httpx.Headers") -> dict[str, str]:
    """Copy the upstream's response headers, keeping retry-after and rate-limit ones.

    Drops the length and encoding headers the server recomputes and content-type,
    so a client sees the upstream's retry-after and anthropic-ratelimit-* headers
    and can back off instead of hammering into a rate limit.
    """
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _DROPPED_RESPONSE_HEADERS
    }


def resolve_thread(request: Request) -> tuple[str, bool]:
    """Return (thread_id, ephemeral): a supplied fixed id, or a fresh ephemeral one."""
    supplied = request.headers.get("x-piighost-thread-id")
    if supplied:
        return supplied, False
    return uuid.uuid4().hex, True


async def forward_json(
    base: str,
    subpath: str,
    headers: dict[str, str],
    body: dict,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """POST a JSON body to the upstream, mapping transport errors to 502.

    Query params from the caller are relayed too, so flags such as `?beta=true`
    reach the upstream and the relay stays transparent.
    """
    try:
        return await _client.post(
            f"{base}/{subpath}", headers=headers, json=body, params=params
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")
