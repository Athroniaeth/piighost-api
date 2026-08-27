"""The app factory registers the Anthropic proxy route."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from litestar import Litestar

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _mock_pipeline() -> MagicMock:
    pipeline = MagicMock()
    pipeline.anonymize = AsyncMock()
    pipeline.deanonymize = AsyncMock()
    pipeline.forget_thread = AsyncMock()
    pipeline.detector = MagicMock()
    return pipeline


def test_app_registers_anthropic_messages_route() -> None:
    """create_app registers /anthropic/v1/messages and /anthropic/v1/messages/count_tokens."""
    config = MagicMock()
    config.name = "test"
    config.detector.type = "exact"
    with patch("piighost_api.app.load_config", return_value=config):
        with patch(
            "piighost_api.app.load_thread_pipeline", return_value=_mock_pipeline()
        ):
            from piighost_api.app import create_app

            app: Litestar = create_app(FIXTURES / "minimal.toml")
    paths = {route.path for route in app.routes}
    assert "/anthropic/v1/messages" in paths
    assert "/anthropic/v1/messages/count_tokens" in paths
