"""Keyshield authentication guard for Litestar."""

import logging
from typing import Any, TypedDict

from keyshield import ApiKeyService
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import BaseRouteHandler

logger = logging.getLogger(__name__)


class AuthState(TypedDict):
    """Mutable auth state shared between the lifespan and the guard.

    The guard closure captures this dict by reference, so the lifespan can
    flip ``enabled`` after loading keys and the already-registered guard
    observes the change. (A plain ``list.append`` of a guard inside the
    lifespan would not work: Litestar freezes per-handler guards at
    construction time.)
    """

    enabled: bool
    svc: ApiKeyService


def create_auth_guard(auth_state: AuthState):
    """Create a Litestar guard that verifies API keys via keyshield.

    The guard is registered unconditionally at construction time. It:

    1. no-ops when ``auth_state["enabled"]`` is falsy (anonymous mode);
    2. skips handlers opted out via ``exclude_from_auth=True``;
    3. otherwise requires an ``Authorization: Bearer <key>`` header and
       verifies the key against the keyshield service.

    Args:
        auth_state: Mutable state dict with ``enabled`` and ``svc`` keys.

    Returns:
        An async guard callable for Litestar's ``guards`` parameter.
    """

    async def auth_guard(
        connection: ASGIConnection[Any, Any, Any, Any],
        route_handler: BaseRouteHandler,
    ) -> None:
        if not auth_state["enabled"]:
            return

        # Litestar guards do not honor exclude_from_auth on their own (that
        # opt is only consulted by authentication MIDDLEWARE), so the guard
        # must skip opted-out handlers explicitly.
        if route_handler.opt.get("exclude_from_auth"):
            return

        auth = connection.headers.get("authorization", "")

        if not auth.startswith("Bearer "):
            raise NotAuthorizedException("Missing or malformed Authorization header")

        api_key = auth.removeprefix("Bearer ")

        try:
            await auth_state["svc"].verify_key(api_key)
        except Exception as exc:
            logger.debug("API key verification failed: %s", exc)
            raise NotAuthorizedException("Invalid API key")

    return auth_guard
