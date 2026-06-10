"""Tests for auth.py — keyshield guard."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from piighost_api.auth import create_auth_guard

from conftest import FIXTURES


def _make_app_with_auth(verify_side_effect=None) -> Litestar:
    """Build a minimal Litestar app with the auth guard enabled."""
    svc = MagicMock()
    svc.verify_key = AsyncMock(side_effect=verify_side_effect)
    guard = create_auth_guard({"enabled": True, "svc": svc})

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


# ------------------------------------------------------------------
# Fail-fast startup: no keys means no boot unless anonymous opt-in
# ------------------------------------------------------------------


def _build_app(mock_pipeline: MagicMock, mock_manifest: MagicMock) -> Litestar:
    """Build the real app with a mocked pipeline (same patching as the app fixture)."""
    with patch(
        "piighost_api.app.load_pipeline", return_value=(mock_pipeline, mock_manifest)
    ):
        from piighost_api.app import create_app

        return create_app(FIXTURES / "minimal.toml")


def test_guard_noops_when_auth_disabled() -> None:
    """When ``enabled`` is False the guard must let everything through."""
    svc = MagicMock()
    svc.verify_key = AsyncMock()
    guard = create_auth_guard({"enabled": False, "svc": svc})

    @get("/protected")
    async def protected() -> dict:
        return {"ok": True}

    app = Litestar(route_handlers=[protected], guards=[guard])
    with TestClient(app=app) as client:
        # No Authorization header, yet the guard is a no-op.
        assert client.get("/protected").status_code == 200
        svc.verify_key.assert_not_called()


def test_startup_fails_without_keys_by_default(
    monkeypatch: pytest.MonkeyPatch,
    mock_pipeline: MagicMock,
    mock_manifest: MagicMock,
) -> None:
    """No API keys and no explicit anonymous opt-in: startup must fail."""
    monkeypatch.delenv("PIIGHOST_ALLOW_ANONYMOUS", raising=False)
    # Minor #5: scrub any ambient API_KEY_* so the environment cannot flip
    # this test into the "keys present" branch and mask a regression.
    for key in list(os.environ):
        if key.startswith("API_KEY_"):
            monkeypatch.delenv(key, raising=False)
    app = _build_app(mock_pipeline, mock_manifest)
    # Litestar's lifespan runs in a task group: the startup error surfaces
    # wrapped in an ExceptionGroup.
    with pytest.raises(ExceptionGroup) as excinfo:
        with TestClient(app=app):
            pass
    assert excinfo.group_contains(RuntimeError, match="PIIGHOST_ALLOW_ANONYMOUS")


def test_startup_allows_anonymous_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    mock_pipeline: MagicMock,
    mock_manifest: MagicMock,
) -> None:
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    app = _build_app(mock_pipeline, mock_manifest)
    with TestClient(app=app) as tc:
        assert tc.get("/health").status_code == 200


# ------------------------------------------------------------------
# End-to-end auth enforcement: the guard must fire at construction
# time (the historical bug appended it inside the lifespan, where the
# mutation was a no-op, leaving every PII endpoint open).
# ------------------------------------------------------------------

# A valid keyshield key string: "<global_prefix>-<key_id>-<key_secret>".
# keyshield re-hashes the secret on load_dotenv and on verify_key, so the
# same literal works both as the configured key and as the Bearer token.
_VALID_KEY = "ak_v1-testkeyid000001-testsecretvaluethatislongenoughforkeyshield123"


def test_protected_route_401s_without_bearer_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_pipeline: MagicMock,
    mock_manifest: MagicMock,
) -> None:
    monkeypatch.setenv("API_KEY_default", _VALID_KEY)
    monkeypatch.delenv("PIIGHOST_ALLOW_ANONYMOUS", raising=False)
    app = _build_app(mock_pipeline, mock_manifest)
    with TestClient(app=app) as tc:
        # No Authorization header: protected routes must be rejected.
        assert (
            tc.post("/v1/anonymize", json={"text": "x", "thread_id": "t"}).status_code
            == 401
        )
        assert tc.delete("/v1/threads/t").status_code == 401
        # Excluded routes stay open.
        assert tc.get("/health").status_code == 200
        assert tc.get("/v1/labels").status_code == 200
        assert tc.get("/").status_code == 200


def test_valid_bearer_is_accepted_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_pipeline: MagicMock,
    mock_manifest: MagicMock,
) -> None:
    monkeypatch.setenv("API_KEY_default", _VALID_KEY)
    monkeypatch.delenv("PIIGHOST_ALLOW_ANONYMOUS", raising=False)
    app = _build_app(mock_pipeline, mock_manifest)
    with TestClient(app=app) as tc:
        ok = tc.post(
            "/v1/anonymize",
            json={"text": "x", "thread_id": "t"},
            headers={"Authorization": f"Bearer {_VALID_KEY}"},
        )
        assert ok.status_code == 201
