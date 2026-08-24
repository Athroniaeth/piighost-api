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
