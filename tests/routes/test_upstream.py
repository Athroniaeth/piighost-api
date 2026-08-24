"""Tests for the OpenAI-proxy upstream helpers."""

import pytest
from litestar.exceptions import HTTPException

from piighost_api.routes._upstream import (
    forward_headers,
    forward_headers_permissive,
    upstream_base_url,
)


class _Headers:
    """A minimal stand-in for Litestar's request.headers mapping."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)


def test_upstream_base_url_reads_the_header() -> None:
    """The upstream base URL comes from X-PIIGhost-Upstream, trailing slash trimmed."""
    headers = _Headers({"x-piighost-upstream": "https://api.openai.com/v1/"})
    assert upstream_base_url(headers) == "https://api.openai.com/v1"


def test_upstream_base_url_missing_raises_400() -> None:
    """A missing upstream header is a 400."""
    headers = _Headers({})
    with pytest.raises(HTTPException) as excinfo:
        upstream_base_url(headers)
    assert excinfo.value.status_code == 400


def test_forward_headers_keeps_auth_and_content_type_only() -> None:
    """Only Authorization and Content-Type are forwarded; piighost headers dropped."""
    headers = _Headers(
        {
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
            "x-piighost-upstream": "https://api.openai.com/v1",
            "x-piighost-thread-id": "t1",
            "host": "proxy.local",
        }
    )
    assert forward_headers(headers) == {
        "authorization": "Bearer sk-test",
        "content-type": "application/json",
    }


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


def test_forward_headers_permissive_relays_unknown_drops_internal() -> None:
    """Permissive forwarding relays arbitrary client headers, dropping hop-by-hop and piighost."""
    headers = {
        "authorization": "Bearer oauth",
        "anthropic-beta": "oauth-2025-04-20",
        "user-agent": "claude-cli/1.2.3",
        "x-app": "cli",
        "x-stainless-lang": "js",
        "host": "localhost:8080",
        "content-length": "123",
        "accept-encoding": "gzip",
        "connection": "keep-alive",
        "x-piighost-upstream": "https://api.anthropic.com/v1",
        "x-piighost-thread-id": "t1",
        "x-piighost-debug": "1",
    }
    forwarded = forward_headers_permissive(headers)
    assert forwarded["authorization"] == "Bearer oauth"
    assert forwarded["anthropic-beta"] == "oauth-2025-04-20"
    assert forwarded["user-agent"] == "claude-cli/1.2.3"
    assert forwarded["x-app"] == "cli"
    assert forwarded["x-stainless-lang"] == "js"
    for dropped in (
        "host",
        "content-length",
        "accept-encoding",
        "connection",
        "x-piighost-upstream",
        "x-piighost-thread-id",
        "x-piighost-debug",
    ):
        assert dropped not in forwarded
    assert not any(name.lower().startswith("x-piighost-") for name in forwarded)
