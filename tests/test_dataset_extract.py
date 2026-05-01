"""Tests for the Langfuse trace -> JSONL record shaping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from piighost_api.dataset.extract import (
    DatasetMode,
    record_from_trace,
)


def _trace(
    *,
    name: str,
    input: dict[str, Any] | None,
    output: dict[str, Any] | None,
    trace_id: str = "tid",
    session_id: str | None = "session",
    created_at: str | None = "2026-05-01T05:47:27.000Z",
    observations: list[Any] | None = None,
) -> Any:
    return SimpleNamespace(
        name=name,
        input=input,
        output=output,
        id=trace_id,
        session_id=session_id,
        createdAt=created_at,
        observations=observations or [],
    )


def test_hitl_trace_yields_record_with_human_entities() -> None:
    trace = _trace(
        name="piighost.hitl_correction",
        input={
            "text": "Bonjour Patrick",
            "labels": ["PERSON"],
            "detections": [
                {
                    "label": "PERSON",
                    "position": [8, 15],
                    "confidence": 0.4,
                    "text": "Patrick",
                }
            ],
        },
        output={
            "detections": [
                {
                    "label": "ORG",
                    "position": [8, 15],
                    "confidence": 1.0,
                    "text": "Patrick",
                }
            ]
        },
    )

    record = record_from_trace(trace, mode=DatasetMode.all)

    assert record is not None
    assert record["text"] == "Bonjour Patrick"
    assert record["entities"] == [[8, 15, "ORG"]]
    assert record["model_entities"] == [[8, 15, "PERSON"]]
    assert record["labels_universe"] == ["PERSON"]
    assert record["source"] == "hitl"
    assert record["trace_id"] == "tid"
    assert record["session_id"] == "session"


def test_anonymize_trace_yields_model_only_record() -> None:
    detect_obs = SimpleNamespace(
        name="piighost.detect",
        output={
            "detections": [{"label": "PERSON", "position": [8, 15], "confidence": 0.9}]
        },
    )
    trace = _trace(
        name="piighost.anonymize_pipeline",
        input={"text": "Bonjour Patrick"},
        output={"text": "Bonjour <<PERSON:1>>", "entity_count": 1},
        observations=[detect_obs],
    )

    record = record_from_trace(trace, mode=DatasetMode.all)

    assert record is not None
    assert record["text"] == "Bonjour Patrick"
    assert record["entities"] == [[8, 15, "PERSON"]]
    assert record["model_entities"] == [[8, 15, "PERSON"]]
    assert record["source"] == "model"


def test_trace_without_input_text_is_skipped() -> None:
    trace = _trace(
        name="piighost.hitl_correction",
        input={"detections": []},
        output={"detections": []},
    )

    assert record_from_trace(trace, mode=DatasetMode.all) is None


def test_mode_hitl_skips_anonymize_traces() -> None:
    trace = _trace(
        name="piighost.anonymize_pipeline",
        input={"text": "Bonjour"},
        output={"text": "Bonjour", "entity_count": 0},
        observations=[],
    )
    assert record_from_trace(trace, mode=DatasetMode.hitl) is None


def test_mode_model_only_skips_hitl_traces() -> None:
    trace = _trace(
        name="piighost.hitl_correction",
        input={"text": "Bonjour", "detections": []},
        output={"detections": []},
    )
    assert record_from_trace(trace, mode=DatasetMode.model_only) is None
