"""A configuration that needs a missing piighost extra fails with a Docker hint."""

from unittest.mock import patch

import pytest

from piighost.config import load_config

from piighost_api.app import MissingExtraError, _thread_pipeline

from conftest import FIXTURES


def _config():
    """A regex configuration, whose build is made to fail in each test."""
    return load_config(FIXTURES / "minimal.toml")


def test_a_missing_extra_names_extra_packages() -> None:
    """piighost's pip hint becomes one for EXTRA_PACKAGES and PIIGHOST_EXTRAS."""
    config = _config()
    error = ImportError(
        "Gliner2Detector requires the gliner2 package. "
        "Install it with: pip install piighost[gliner2]"
    )
    with patch.object(type(config), "build", side_effect=error):
        with pytest.raises(MissingExtraError) as caught:
            _thread_pipeline(config)
    message = str(caught.value)
    assert 'EXTRA_PACKAGES="piighost[gliner2]"' in message
    assert "--build-arg PIIGHOST_EXTRAS=gliner2" in message
    assert caught.value.__cause__ is error


def test_another_import_error_is_left_alone() -> None:
    """An import error that names no piighost extra propagates unchanged."""
    config = _config()
    error = ImportError("No module named 'something_else'")
    with patch.object(type(config), "build", side_effect=error):
        with pytest.raises(ImportError) as caught:
            _thread_pipeline(config)
    assert caught.value is error
