"""Shared fixtures for piighost-api tests."""

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from piighost.components.anonymizer.base import Anonymization
from piighost.conversation_memory import Forgotten
from piighost.models import Detection, Entity, Span

FIXTURES = Path(__file__).parent / "fixtures"


def _make_entity(
    text: str, label: str, start: int, end: int, confidence: float = 0.95
) -> Entity:
    detection = Detection(
        span=Span(start, end),
        text=text,
        label=label,
        confidence=confidence,
    )
    return Entity(detections=(detection,))


ENTITY_PERSON = _make_entity("Patrick", "PERSON", 0, 7)
ENTITY_LOCATION = _make_entity("Paris", "LOCATION", 17, 22, confidence=0.92)

TOKENS: dict[Entity, str] = {
    ENTITY_PERSON: "<<PERSON:1>>",
    ENTITY_LOCATION: "<<LOCATION:1>>",
}


@pytest.fixture
def mock_pipeline() -> MagicMock:
    """Mock ThreadAnonymizationPipeline exposing the v2 async API.

    anonymize/anonymize_corrected return an Anonymization (text + entity-to-token
    map), deanonymize returns a plain string, forget_thread returns a Forgotten,
    and the detect preview reads the pipeline's detector and linker.
    """
    pipeline = MagicMock()

    pipeline.anonymize = AsyncMock(
        return_value=Anonymization(
            text="<<PERSON:1>> habite à <<LOCATION:1>>",
            tokens=dict(TOKENS),
        )
    )
    pipeline.anonymize_corrected = AsyncMock(
        return_value=Anonymization(
            text="<<PERSON:1>> habite à <<LOCATION:1>>",
            tokens=dict(TOKENS),
        )
    )
    pipeline.deanonymize = AsyncMock(return_value="Patrick habite à Paris")
    pipeline.forget_thread = AsyncMock(return_value=Forgotten(messages=2, detections=3))
    pipeline.thread_token_map = AsyncMock(
        return_value={"<<PERSON:1>>": "Patrick", "<<LOCATION:1>>": "Paris"}
    )

    pipeline.detector = MagicMock()
    pipeline.detector.detect = AsyncMock(
        return_value=[ENTITY_PERSON.detections[0], ENTITY_LOCATION.detections[0]]
    )
    pipeline.linker = MagicMock()
    pipeline.linker.link = MagicMock(return_value=[ENTITY_PERSON, ENTITY_LOCATION])

    return pipeline


@pytest.fixture
def mock_config() -> MagicMock:
    """Mock PipelineConfig, source of the /health and /v1/labels metadata."""
    config = MagicMock()
    config.name = "test"
    config.detector.type = "regex"
    # The /v1/labels route derives its vocabulary from the detector config, so
    # give the regex mock a real pattern set and no catalogs.
    config.detector.patterns = {"EMAIL": "x", "PHONE": "y"}
    config.detector.catalogs = []
    return config


@pytest.fixture
def allow_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt in to anonymous mode so route tests keep their no-auth behavior.

    Strict-mode startup tests must NOT use this fixture (they need the
    variable absent).
    """
    monkeypatch.setenv("PIIGHOST_ALLOW_ANONYMOUS", "true")
    monkeypatch.delenv("PIIGHOST_RATE_LIMIT", raising=False)
    # Scrub any ambient API_KEY_* so a developer's exported keys cannot
    # flip these open-route tests into enforcing auth (keyshield would
    # load them and enable the guard despite anonymous mode).
    for key in list(os.environ):
        if key.startswith("API_KEY_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def app(
    mock_pipeline: MagicMock,
    mock_config: MagicMock,
    allow_anonymous: None,
) -> Litestar:
    """Create a Litestar app with a mock pipeline (bypasses config loading)."""
    with (
        patch("piighost_api.app.load_config", return_value=mock_config),
        patch("piighost_api.app.load_thread_pipeline", return_value=mock_pipeline),
    ):
        from piighost_api.app import create_app

        return create_app(FIXTURES / "minimal.toml")


@pytest.fixture
def client(app: Litestar) -> Generator[TestClient, None, None]:
    """Litestar sync test client."""
    with TestClient(app=app) as tc:
        yield tc
