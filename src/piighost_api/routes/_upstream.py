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

    def get(self, key: str, /) -> str | None: ...


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
