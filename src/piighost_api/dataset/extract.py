"""Pure extraction logic: Langfuse trace -> JSONL record dict.

The CLI module wires these functions to a real Langfuse client and
writes the records to disk. The functions in this module take
quack-typed objects (anything with the right attributes / keys) so
tests can drive them with simple namespaces.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class DatasetMode(str, Enum):
    """Which Langfuse trace types end up in the dataset."""

    all = "all"
    hitl = "hitl"
    model_only = "model-only"


HITL_TRACE_NAME = "piighost.hitl_correction"
ANONYMIZE_TRACE_NAME = "piighost.anonymize_pipeline"
DETECT_OBS_NAME = "piighost.detect"


def _entities_from_detections(
    detections: list[dict[str, Any]] | None,
) -> list[list[Any]]:
    """Convert a list of detection dicts into ``[[start, end, label], ...]``."""
    if not detections:
        return []
    out: list[list[Any]] = []
    for det in detections:
        position = det.get("position") or [det.get("start_pos"), det.get("end_pos")]
        if position is None or position[0] is None or position[1] is None:
            continue
        label = det.get("label")
        if label is None:
            continue
        out.append([int(position[0]), int(position[1]), str(label)])
    return out


def _detect_obs_for(trace: Any) -> Any | None:
    """Return the ``piighost.detect`` child observation from *trace*, or None."""
    observations = getattr(trace, "observations", None) or []
    for obs in observations:
        if getattr(obs, "name", None) == DETECT_OBS_NAME:
            return obs
    return None


def record_from_trace(trace: Any, *, mode: DatasetMode) -> dict[str, Any] | None:
    """Build a JSONL record from a Langfuse trace, or ``None`` if it should be skipped.

    A trace is skipped when:

    * its ``name`` does not match the active ``mode``,
    * its ``input.text`` is missing or empty (older traces predate the
      raw-text-by-default lib change),
    * for ``model-only`` records, its ``piighost.detect`` child
      observation is missing.
    """
    name = getattr(trace, "name", None)
    if mode is DatasetMode.hitl and name != HITL_TRACE_NAME:
        return None
    if mode is DatasetMode.model_only and name != ANONYMIZE_TRACE_NAME:
        return None
    if name not in (HITL_TRACE_NAME, ANONYMIZE_TRACE_NAME):
        return None

    raw_input = getattr(trace, "input", None) or {}
    if not isinstance(raw_input, dict):
        return None
    text = raw_input.get("text")
    if not isinstance(text, str) or not text:
        return None

    if name == HITL_TRACE_NAME:
        raw_output = getattr(trace, "output", None) or {}
        if not isinstance(raw_output, dict):
            return None
        human_entities = _entities_from_detections(raw_output.get("detections"))
        model_entities = _entities_from_detections(raw_input.get("detections"))
        labels_universe = list(raw_input.get("labels") or [])
        source = "hitl"
        entities = human_entities
    else:
        detect_obs = _detect_obs_for(trace)
        if detect_obs is None:
            return None
        detect_output = getattr(detect_obs, "output", None) or {}
        if not isinstance(detect_output, dict):
            return None
        model_entities = _entities_from_detections(detect_output.get("detections"))
        human_entities = list(model_entities)
        labels_universe = []
        source = "model"
        entities = human_entities

    return {
        "text": text,
        "entities": entities,
        "model_entities": model_entities,
        "labels_universe": labels_universe,
        "source": source,
        "trace_id": getattr(trace, "id", None),
        "session_id": getattr(trace, "session_id", None),
        "created_at": getattr(trace, "createdAt", None),
    }
