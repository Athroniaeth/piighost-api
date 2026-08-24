"""Helpers for the OpenAI proxy's outbound relay.

The upstream is chosen per request via the X-PIIGhost-Upstream header, and the
caller's Authorization is forwarded to it, so the proxy is a transparent relay.
"""

from collections.abc import Iterable
from typing import Protocol

from litestar.exceptions import HTTPException

UPSTREAM_HEADER = "x-piighost-upstream"
THREAD_HEADER = "x-piighost-thread-id"

_FORWARDED = (
    "authorization",
    "content-type",
    "x-api-key",
    "anthropic-version",
    "anthropic-beta",
)
"""Header names passed through to the upstream; covers both OpenAI (authorization)
and Anthropic (x-api-key, anthropic-version, anthropic-beta) auth conventions."""

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
"""RFC 7230 hop-by-hop headers, which belong to a single transport hop only."""

_DROPPED_PERMISSIVE = _HOP_BY_HOP | {
    "host",
    "content-length",
    "accept-encoding",
}
"""Headers the permissive forwarder never relays: hop-by-hop, plus the ones httpx
must recompute for the rewritten body (host, content-length, accept-encoding).
Every piighost control header is dropped by prefix, see _PIIGHOST_PREFIX."""

_PIIGHOST_PREFIX = "x-piighost-"
"""Any header under this prefix is internal control and must not reach the upstream."""


class _HeaderMap(Protocol):
    """The subset of a headers mapping these helpers read."""

    def get(self, key: str, /) -> str | None: ...


class _HeaderItems(Protocol):
    """A headers mapping the permissive forwarder can iterate over."""

    def items(self) -> Iterable[tuple[str, str]]: ...


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


def forward_headers(headers: _HeaderMap) -> dict[str, str]:
    """Keep only the headers the upstream needs, dropping piighost and hop-by-hop."""
    forwarded: dict[str, str] = {}
    for name in _FORWARDED:
        value = headers.get(name)
        if value is not None:
            forwarded[name] = value
    return forwarded


def forward_headers_permissive(headers: _HeaderItems) -> dict[str, str]:
    """Relay every client header except hop-by-hop, recomputed, and piighost ones.

    Unlike forward_headers, this keeps arbitrary client headers such as the
    Claude Code user-agent and OAuth beta flags, which the Anthropic upstream may
    require to validate the caller. Header names match case-insensitively.
    """
    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _DROPPED_PERMISSIVE or lowered.startswith(_PIIGHOST_PREFIX):
            continue
        forwarded[name] = value
    return forwarded
