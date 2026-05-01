"""Pure metrics computation over a HITL JSONL dataset.

Aggregates per-label TP / FP / FN with strict or lenient (IoU)
matching, surfaces a label-confusion matrix for spans where model and
human disagree on the label, and supports filtering by record source
(``hitl``, ``model``, ``all``).
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MatchMode(str, Enum):
    strict = "strict"
    lenient = "lenient"


class SourceFilter(str, Enum):
    all = "all"
    hitl = "hitl"
    model = "model"


class OutputFormat(str, Enum):
    table = "table"
    csv = "csv"
    json = "json"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    label: str

    def iou(self, other: "Span") -> float:
        inter_start = max(self.start, other.start)
        inter_end = min(self.end, other.end)
        inter = max(0, inter_end - inter_start)
        union = max(self.end, other.end) - min(self.start, other.start)
        return inter / union if union > 0 else 0.0


@dataclass
class LabelStats:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _parse(items: list[list[Any]] | None) -> list[Span]:
    if not items:
        return []
    out: list[Span] = []
    for item in items:
        if len(item) < 3 or item[2] is None:
            continue
        out.append(Span(int(item[0]), int(item[1]), str(item[2])))
    return out


def _match_strict(
    model: list[Span], human: list[Span]
) -> tuple[list[tuple[Span, Span]], list[Span], list[Span]]:
    keyed = {(s.start, s.end, s.label): s for s in human}
    matches: list[tuple[Span, Span]] = []
    model_only: list[Span] = []
    consumed: set[tuple[int, int, str]] = set()
    for m in model:
        key = (m.start, m.end, m.label)
        if key in keyed and key not in consumed:
            matches.append((m, keyed[key]))
            consumed.add(key)
        else:
            model_only.append(m)
    human_only = [h for h in human if (h.start, h.end, h.label) not in consumed]
    return matches, model_only, human_only


def _match_lenient(
    model: list[Span], human: list[Span], iou_threshold: float
) -> tuple[list[tuple[Span, Span]], list[Span], list[Span]]:
    pairs: list[tuple[float, int, int]] = []
    for i, m in enumerate(model):
        for j, h in enumerate(human):
            if m.label != h.label:
                continue
            score = m.iou(h)
            if score >= iou_threshold:
                pairs.append((score, i, j))
    pairs.sort(reverse=True)

    matched_model: set[int] = set()
    matched_human: set[int] = set()
    matches: list[tuple[Span, Span]] = []
    for _, i, j in pairs:
        if i in matched_model or j in matched_human:
            continue
        matches.append((model[i], human[j]))
        matched_model.add(i)
        matched_human.add(j)
    model_only = [m for i, m in enumerate(model) if i not in matched_model]
    human_only = [h for j, h in enumerate(human) if j not in matched_human]
    return matches, model_only, human_only


def aggregate(
    records: list[dict[str, Any]],
    *,
    match_mode: MatchMode = MatchMode.strict,
    source_filter: SourceFilter = SourceFilter.all,
    iou_threshold: float = 0.5,
) -> tuple[dict[str, LabelStats], dict[tuple[str, str], int]]:
    per_label: dict[str, LabelStats] = defaultdict(LabelStats)
    confusion: dict[tuple[str, str], int] = defaultdict(int)

    for rec in records:
        if source_filter is not SourceFilter.all:
            if rec.get("source") != source_filter.value:
                continue
        model = _parse(rec.get("model_entities"))
        human = _parse(rec.get("entities"))

        if match_mode is MatchMode.strict:
            matches, model_only, human_only = _match_strict(model, human)
        else:
            matches, model_only, human_only = _match_lenient(
                model, human, iou_threshold
            )

        for m, _ in matches:
            per_label[m.label].tp += 1
        for m in model_only:
            same_span = next(
                (h for h in human_only if h.start == m.start and h.end == m.end),
                None,
            )
            if same_span is not None:
                confusion[(m.label, same_span.label)] += 1
            per_label[m.label].fp += 1
        for h in human_only:
            per_label[h.label].fn += 1

    return dict(per_label), dict(confusion)


def macro_avg(per_label: dict[str, LabelStats]) -> dict[str, float]:
    if not per_label:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    n = len(per_label)
    return {
        "precision": sum(s.precision for s in per_label.values()) / n,
        "recall": sum(s.recall for s in per_label.values()) / n,
        "f1": sum(s.f1 for s in per_label.values()) / n,
    }


def micro_avg(per_label: dict[str, LabelStats]) -> dict[str, float]:
    tp = sum(s.tp for s in per_label.values())
    fp = sum(s.fp for s in per_label.values())
    fn = sum(s.fn for s in per_label.values())
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def render_table(
    per_label: dict[str, LabelStats], confusion: dict[tuple[str, str], int]
) -> str:
    if not per_label:
        return "(no records)"
    header = f"{'label':<20s} {'tp':>6} {'fp':>6} {'fn':>6} {'P':>6} {'R':>6} {'F1':>6}"
    sep = "-" * len(header)
    lines = [header, sep]
    for label in sorted(per_label):
        s = per_label[label]
        lines.append(
            f"{label:<20s} {s.tp:>6d} {s.fp:>6d} {s.fn:>6d}"
            f" {s.precision:>6.2f} {s.recall:>6.2f} {s.f1:>6.2f}"
        )
    lines.append(sep)
    macro = macro_avg(per_label)
    micro = micro_avg(per_label)
    lines.append(
        f"{'macro avg':<20s} {'-':>6} {'-':>6} {'-':>6}"
        f" {macro['precision']:>6.2f} {macro['recall']:>6.2f} {macro['f1']:>6.2f}"
    )
    lines.append(
        f"{'micro avg':<20s} {'-':>6} {'-':>6} {'-':>6}"
        f" {micro['precision']:>6.2f} {micro['recall']:>6.2f} {micro['f1']:>6.2f}"
    )
    if confusion:
        lines.append("")
        lines.append("Label confusion (model -> human, same span):")
        for (m, h), n in sorted(confusion.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {m} -> {h}: {n}")
    return "\n".join(lines)


def render_csv(per_label: dict[str, LabelStats]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["label", "tp", "fp", "fn", "precision", "recall", "f1"])
    for label in sorted(per_label):
        s = per_label[label]
        w.writerow(
            [
                label,
                s.tp,
                s.fp,
                s.fn,
                f"{s.precision:.4f}",
                f"{s.recall:.4f}",
                f"{s.f1:.4f}",
            ]
        )
    macro = macro_avg(per_label)
    micro = micro_avg(per_label)
    w.writerow([])
    w.writerow(
        [
            "macro avg",
            "",
            "",
            "",
            f"{macro['precision']:.4f}",
            f"{macro['recall']:.4f}",
            f"{macro['f1']:.4f}",
        ]
    )
    w.writerow(
        [
            "micro avg",
            "",
            "",
            "",
            f"{micro['precision']:.4f}",
            f"{micro['recall']:.4f}",
            f"{micro['f1']:.4f}",
        ]
    )
    return buf.getvalue()


def render_json(
    per_label: dict[str, LabelStats], confusion: dict[tuple[str, str], int]
) -> str:
    nested: dict[str, dict[str, int]] = defaultdict(dict)
    for (m, h), n in confusion.items():
        nested[m][h] = n
    payload = {
        "per_label": {
            label: {
                "tp": s.tp,
                "fp": s.fp,
                "fn": s.fn,
                "precision": s.precision,
                "recall": s.recall,
                "f1": s.f1,
            }
            for label, s in per_label.items()
        },
        "macro_avg": macro_avg(per_label),
        "micro_avg": micro_avg(per_label),
        "label_confusion": dict(nested),
    }
    return json.dumps(payload, indent=2)
