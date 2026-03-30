"""Tests for auth.py — keyshield guard."""

from unittest.mock import AsyncMock, MagicMock

from litestar import Litestar, get
from litestar.testing import TestClient

from piighost_api.auth import create_auth_guard


def _make_app_with_auth(verify_side_effect=None) -> Litestar:
    """Build a minimal Litestar app with the auth guard."""
    svc = MagicMock()
    svc.verify_key = AsyncMock(side_effect=verify_side_effect)
    guard = create_auth_guard(svc)

    @get("/protected")
    async def protected() -> dict:
        return {"ok": True}

    return Litestar(route_handlers=[protected], guards=[guard])


def test_valid_bearer_token() -> None:
    app = _make_app_with_auth(verify_side_effect=None)
    with TestClient(app=app) as client:
        response = client.get(
            "/protected", headers={"Authorization": "Bearer valid-key"}
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_missing_auth_header() -> None:
    app = _make_app_with_auth()
    with TestClient(app=app, raise_server_exceptions=False) as client:
        response = client.get("/protected")
        assert response.status_code == 401


def test_malformed_auth_header() -> None:
    app = _make_app_with_auth()
    with TestClient(app=app, raise_server_exceptions=False) as client:
        response = client.get("/protected", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 401


def test_invalid_api_key() -> None:
    app = _make_app_with_auth(verify_side_effect=Exception("bad key"))
    with TestClient(app=app, raise_server_exceptions=False) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer bad-key"})
        assert response.status_code == 401
