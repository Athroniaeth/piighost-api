"""Observation backend resolution for piighost-api.

Reads environment variables to decide which piighost observation backend
to instantiate (Langfuse, Opik, …). The loader exposes three layers:

* :func:`detect_observation_backend` — pure env-var inspection that
  returns an :class:`ObservationBackend` enum value.
* :func:`create_observation_service` — factory that maps an enum value
  to a concrete ``AbstractObservationService`` (or ``None``).
* :func:`load_observation_service` — convenience wrapper composing the
  two for use at app startup.

If two or more backend "switch" env vars are set simultaneously, the
loader raises :class:`MultipleObservationBackendsError` so the server
fails fast at boot rather than silently picking one.
"""

from __future__ import annotations

import os
from enum import Enum

from piighost.observation import AbstractObservationService


class ObservationBackend(str, Enum):
    """Identifier for the observation backend in use."""

    NONE = "none"
    LANGFUSE = "langfuse"
    OPIK = "opik"
    PHOENIX = "phoenix"


class MultipleObservationBackendsError(RuntimeError):
    """Raised when more than one observation backend is configured."""


_BACKEND_ENV_SWITCHES: dict[ObservationBackend, str] = {
    ObservationBackend.LANGFUSE: "LANGFUSE_PUBLIC_KEY",
    ObservationBackend.OPIK: "OPIK_API_KEY",
}


def detect_observation_backend() -> ObservationBackend:
    """Resolve the observation backend from environment variables.

    Each backend has a "switch" env var (the SDK's own primary credential
    variable) whose presence opts the backend in. Exactly one switch may
    be set at a time.

    Returns:
        The matching :class:`ObservationBackend`, or
        :attr:`ObservationBackend.NONE` if no switch is set.

    Raises:
        MultipleObservationBackendsError: When two or more switches are
            set, since piighost only accepts a single observation
            service per pipeline.
    """
    detected = [
        backend
        for backend, env_var in _BACKEND_ENV_SWITCHES.items()
        if os.getenv(env_var)
    ]

    if len(detected) > 1:
        names = ", ".join(b.value for b in detected)
        raise MultipleObservationBackendsError(
            f"Multiple observation backends configured ({names}); "
            f"set environment variables for only one of: "
            f"{', '.join(_BACKEND_ENV_SWITCHES.values())}."
        )

    if not detected:
        return ObservationBackend.NONE

    return detected[0]


def create_observation_service(
    backend: ObservationBackend,
) -> AbstractObservationService | None:
    """Instantiate the concrete service matching *backend*.

    Args:
        backend: Backend identifier returned by
            :func:`detect_observation_backend`.

    Returns:
        A live ``AbstractObservationService`` instance, or ``None`` when
        *backend* is :attr:`ObservationBackend.NONE` (the pipeline keeps
        its default ``NoOpObservationService``).

    Raises:
        ImportError: When the chosen backend's SDK extra is not
            installed (the piighost adapter raises this with an
            explicit ``piighost[<backend>]`` install hint).
        NotImplementedError: When *backend* identifies a service that
            piighost does not yet provide (e.g. Phoenix).
    """
    if backend is ObservationBackend.NONE:
        return None

    if backend is ObservationBackend.LANGFUSE:
        # Import the piighost adapter first so its top-level ``find_spec``
        # check raises a helpful ``install piighost[langfuse]`` message
        # before Python's own ``ModuleNotFoundError`` on ``langfuse``.
        from piighost.observation.langfuse import LangfuseObservationService
        from langfuse import Langfuse  # pyrefly: ignore[missing-import]

        return LangfuseObservationService(client=Langfuse())

    if backend is ObservationBackend.OPIK:
        from piighost.observation.opik import OpikObservationService
        from opik import Opik  # pyrefly: ignore[missing-import]

        return OpikObservationService(client=Opik())

    if backend is ObservationBackend.PHOENIX:
        raise NotImplementedError(
            "Phoenix observation backend is not yet implemented in piighost."
        )

    raise ValueError(f"Unknown observation backend: {backend!r}")


def load_observation_service() -> AbstractObservationService | None:
    """Detect and instantiate the observation backend from environment.

    Convenience wrapper that calls :func:`detect_observation_backend`
    and feeds the result into :func:`create_observation_service`.
    """
    backend = detect_observation_backend()
    return create_observation_service(backend)
