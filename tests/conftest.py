"""Shared fixtures for piighost-api tests."""

import os

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from piighost.models import Detection, Entity, Span
from piighost.placeholder import LabelCounterPlaceholderFactory

FIXTURES = Path(__file__).parent / "fixtures"


def _make_entity(
    text: str, label: str, start: int, end: int, confidence: float = 0.95
) -> Entity:
    return Entity(
        detections=(
            Detection(
                text=text,
                label=label,
                position=Span(start, end),
                confidence=confidence,
            ),
        )
    )


ENTITY_PERSON = _make_entity(
    "Patrick",
    "PERSON",
    0,
    7,
)
ENTITY_LOCATION = _make_entity(
    "Paris",
    "LOCATION",
    17,
    22,
    confidence=0.92,
)


@pytest.fixture
def mock_pipeline() -> MagicMock:
    """Mock ThreadAnonymizationPipeline with async methods."""
    pipeline = MagicMock()

    pipeline.anonymize = AsyncMock(
        return_value=(
            "<<PERSON:1>> habite à <<LOCATION:1>>",
            [ENTITY_PERSON, ENTITY_LOCATION],
        )
    )
    pipeline.deanonymize = AsyncMock(
        return_value=("Patrick habite à Paris", [ENTITY_PERSON, ENTITY_LOCATION])
    )
    pipeline.deanonymize_with_ent = AsyncMock(return_value="Patrick aime Paris")

    pipeline.detect_entities = AsyncMock(return_value=[ENTITY_PERSON, ENTITY_LOCATION])
    pipeline.forget_thread = AsyncMock()

    pipeline.get_resolved_entities = MagicMock(
        return_value=[ENTITY_PERSON, ENTITY_LOCATION]
    )
    pipeline.get_resolved_tokens = MagicMock(
        return_value={
            ENTITY_PERSON: "<<PERSON:1>>",
            ENTITY_LOCATION: "<<LOCATION:1>>",
        }
    )

    ph_factory = LabelCounterPlaceholderFactory()
    pipeline.ph_factory = ph_factory

    pipeline._detector = MagicMock()
    pipeline._detector.labels = ["PERSON", "LOCATION"]

    return pipeline


@pytest.fixture
def mock_manifest() -> MagicMock:
    """Mock pipeline manifest returned alongside the pipeline by load_pipeline."""
    manifest = MagicMock()
    manifest.name = "test"
    manifest.schema_version = 1
    detector = MagicMock()
    detector.name = "default"
    detector.type = "exact"
    detector.labels = ["PERSON", "LOCATION"]
    manifest.detectors = [detector]
    return manifest


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
    mock_manifest: MagicMock,
    allow_anonymous: None,
) -> Litestar:
    """Create a Litestar app with mock pipeline (bypasses load_pipeline)."""
    with patch(
        "piighost_api.app.load_pipeline", return_value=(mock_pipeline, mock_manifest)
    ):
        from piighost_api.app import create_app

        return create_app(FIXTURES / "minimal.toml")


@pytest.fixture
def client(app: Litestar) -> Generator[TestClient, None, None]:
    """Litestar sync test client."""
    with TestClient(app=app) as tc:
        yield tc
