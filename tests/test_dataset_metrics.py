"""Tests for the metrics computation: aggregation + source filtering."""

from __future__ import annotations

from piighost_api.dataset.metrics import (
    LabelStats,
    MatchMode,
    SourceFilter,
    aggregate,
)


HITL_RECORD = {
    "text": "Bonjour Patrick",
    "entities": [[8, 15, "PERSON"]],
    "model_entities": [[8, 15, "ORG"]],
    "labels_universe": ["PERSON"],
    "source": "hitl",
    "trace_id": "h1",
    "session_id": "s1",
    "created_at": "2026-05-01T00:00:00Z",
}

MODEL_RECORD = {
    "text": "Hello John",
    "entities": [[6, 10, "PERSON"]],
    "model_entities": [[6, 10, "PERSON"]],
    "labels_universe": [],
    "source": "model",
    "trace_id": "m1",
    "session_id": "s2",
    "created_at": "2026-05-01T00:01:00Z",
}


def test_strict_match_counts_tp_fp_fn_per_label() -> None:
    per_label, _ = aggregate(
        [HITL_RECORD, MODEL_RECORD],
        match_mode=MatchMode.strict,
        source_filter=SourceFilter.all,
    )

    # HITL record: model said ORG @ 8-15, human said PERSON @ 8-15.
    # That's a label-changed: ORG counts as fp, PERSON as fn.
    # MODEL record: model == human => PERSON tp.
    assert per_label["PERSON"].tp == 1
    assert per_label["PERSON"].fn == 1
    assert per_label["ORG"].fp == 1
    assert per_label["ORG"].tp == 0


def test_label_changed_records_a_confusion_pair() -> None:
    _, confusion = aggregate(
        [HITL_RECORD],
        match_mode=MatchMode.strict,
        source_filter=SourceFilter.all,
    )
    assert confusion[("ORG", "PERSON")] == 1


def test_source_filter_hitl_excludes_model_record() -> None:
    per_label, _ = aggregate(
        [HITL_RECORD, MODEL_RECORD],
        match_mode=MatchMode.strict,
        source_filter=SourceFilter.hitl,
    )
    # The MODEL record should not show up (it would have produced PERSON tp).
    assert per_label["PERSON"].tp == 0
    assert per_label["PERSON"].fn == 1
    assert per_label["ORG"].fp == 1


def test_source_filter_model_excludes_hitl_record() -> None:
    per_label, confusion = aggregate(
        [HITL_RECORD, MODEL_RECORD],
        match_mode=MatchMode.strict,
        source_filter=SourceFilter.model,
    )
    assert per_label["PERSON"].tp == 1
    assert per_label["PERSON"].fn == 0
    assert "ORG" not in per_label
    assert confusion == {}


def test_label_stats_precision_recall_f1() -> None:
    s = LabelStats(tp=3, fp=1, fn=2)
    assert s.precision == 3 / 4
    assert s.recall == 3 / 5
    assert abs(s.f1 - (2 * (3 / 4) * (3 / 5) / ((3 / 4) + (3 / 5)))) < 1e-9
