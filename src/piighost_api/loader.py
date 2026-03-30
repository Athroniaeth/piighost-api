"""Dynamic pipeline loader (module:variable pattern like uvicorn)."""

import importlib
import logging
import sys
from pathlib import Path

from piighost.pipeline.thread import ThreadAnonymizationPipeline


def load_pipeline(path: str) -> ThreadAnonymizationPipeline:
    """Load a ``ThreadAnonymizationPipeline`` from a ``module:variable`` string.

    The current working directory is added to ``sys.path`` so that local
    modules (e.g. ``pipeline:pipeline``) can be resolved without installation.

    Args:
        path: Import path in ``module:variable`` format
            (e.g. ``"myconfig:pipeline"``).

    Returns:
        The loaded ``ThreadAnonymizationPipeline`` instance.

    Raises:
        ValueError: If *path* does not contain a ``:`` separator.
        ImportError: If the module cannot be imported.
        AttributeError: If the variable does not exist in the module.
        TypeError: If the variable is not a ``ThreadAnonymizationPipeline``.
    """
    logging.info(f"Loading pipeline from {path}")
    if ":" not in path:
        raise ValueError(
            f"Expected 'module:variable' format, got {path!r}. "
            f"Example: piighost-api serve myconfig:pipeline"
        )

    module_path, _, variable = path.partition(":")

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    module = importlib.import_module(module_path)
    obj = getattr(module, variable)

    if not isinstance(obj, ThreadAnonymizationPipeline):
        raise TypeError(
            f"Expected ThreadAnonymizationPipeline, got {type(obj).__name__}. "
            f"Check that {module_path}.{variable} is a ThreadAnonymizationPipeline instance."
        )

    return obj
