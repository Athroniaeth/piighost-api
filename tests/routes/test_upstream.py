"""Tests for the OpenAI-proxy upstream helpers."""

import pytest
from litestar.exceptions import HTTPException

from piighost_api.routes._upstream import forward_headers, upstream_base_url


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
