"""The server refuses to restore through a placeholder factory it cannot reverse."""

import pytest
from litestar.testing import TestClient

from piighost.components.detector import ExactMatchDetector
from piighost.components.anonymizer import Anonymizer
from piighost.components.placeholder import (
    LabelCounterPlaceholderFactory,
    LabelHashPlaceholderFactory,
    LabelPlaceholderFactory,
    MaskPlaceholderFactory,
    RedactPlaceholderFactory,
)
from piighost.components.placeholder.base import AnyPlaceholderFactory
from piighost.pipeline import ThreadAnonymizationPipeline

from piighost_api.app import create_app
from piighost_api.reversible import NonReversibleFactoryError, is_reversible
from piighost_api.routes.anthropic import build_anthropic_router
from piighost_api.routes.openai import build_openai_router

from conftest import FIXTURES

_SECRET = "postgres://app:s3cret@db.internal/shop"


def _pipeline(factory: AnyPlaceholderFactory) -> ThreadAnonymizationPipeline:
    detector = ExactMatchDetector({_SECRET: "DATABASE_URL", "Patrick": "PERSON"})
    return ThreadAnonymizationPipeline(detector, anonymizer=Anonymizer(factory))


@pytest.mark.parametrize(
    "factory",
    [LabelCounterPlaceholderFactory(), LabelHashPlaceholderFactory()],
)
def test_identity_factories_are_reversible(factory: AnyPlaceholderFactory) -> None:
    assert is_reversible(_pipeline(factory))


@pytest.mark.parametrize(
    "factory",
    [RedactPlaceholderFactory(), LabelPlaceholderFactory(), MaskPlaceholderFactory()],
)
def test_collapsing_factories_are_not_reversible(
    factory: AnyPlaceholderFactory,
) -> None:
    assert not is_reversible(_pipeline(factory))


async def test_redact_restores_the_wrong_value() -> None:
    """The bug the guard prevents: every <<REDACT>> restores to one value."""
    pipeline = _pipeline(RedactPlaceholderFactory())
    result = await pipeline.anonymize(f"Patrick connects to {_SECRET}", "t")
    assert result.text == "<<REDACT>> connects to <<REDACT>>"
    # A greeting to Patrick comes back carrying the database password.
    restored = await pipeline.deanonymize("Hello <<REDACT>>", "t")
    assert restored == f"Hello {_SECRET}"


@pytest.mark.parametrize("build", [build_openai_router, build_anthropic_router])
def test_proxy_router_refuses_redact(build) -> None:
    with pytest.raises(NonReversibleFactoryError, match="RedactPlaceholderFactory"):
        build(_pipeline(RedactPlaceholderFactory()))


def test_server_refuses_to_start_on_a_redact_config(allow_anonymous: None) -> None:
    """A redact configuration with the proxies served stops the server at start."""
    with pytest.raises(NonReversibleFactoryError) as caught:
        create_app(FIXTURES / "redact.toml")
    message = str(caught.value)
    assert "RedactPlaceholderFactory" in message
    assert "PIIGHOST_ONE_WAY" in message


def test_one_way_serves_anonymize_without_restoring_routes(
    allow_anonymous: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PIIGHOST_ONE_WAY starts a redact server with no route that restores."""
    monkeypatch.setenv("PIIGHOST_ONE_WAY", "true")
    app = create_app(FIXTURES / "redact.toml")
    with TestClient(app=app) as tc:
        response = tc.post(
            "/v1/anonymize", json={"text": f"Patrick, {_SECRET}", "thread_id": "t"}
        )
        assert response.status_code == 201
        assert response.json()["anonymized_text"] == "<<REDACT>>, <<REDACT>>"
        for method, path in [
            ("POST", "/v1/deanonymize"),
            ("GET", "/v1/threads/t/tokens"),
            ("POST", "/openai/v1/chat/completions"),
            ("POST", "/anthropic/v1/messages"),
        ]:
            assert tc.request(method, path, json={}).status_code == 404, path
