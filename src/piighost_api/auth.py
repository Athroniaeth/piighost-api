"""Keyshield authentication guard for Litestar."""

import logging
from typing import Any

from keyshield import ApiKeyService
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import BaseRouteHandler

logger = logging.getLogger(__name__)


def create_auth_guard(svc: ApiKeyService):
    """Create a Litestar guard that verifies API keys via keyshield.

    Expects an ``Authorization: Bearer <key>`` header on every request.

    Args:
        svc: A configured ``ApiKeyService`` instance.

    Returns:
        An async guard callable for Litestar's ``guards`` parameter.
    """

    async def auth_guard(
        connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler
    ) -> None:
        auth = connection.headers.get("authorization", "")

        if not auth.startswith("Bearer "):
            raise NotAuthorizedException("Missing or malformed Authorization header")

        api_key = auth.removeprefix("Bearer ")

        try:
            await svc.verify_key(api_key)
        except Exception as exc:
            logger.debug("API key verification failed: %s", exc)
            raise NotAuthorizedException("Invalid API key")

    return auth_guard
