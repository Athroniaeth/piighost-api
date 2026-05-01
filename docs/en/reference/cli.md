---
icon: lucide/terminal
---

# CLI

`piighost-api` ships a Typer CLI with three subcommands.

```text
piighost-api serve         <pipeline> [options]
piighost-api dataset extract --output FILE [options]
piighost-api dataset metrics --input FILE  [options]
```

Run `piighost-api --help` (or any subcommand with `--help`) for the live help banner.

---

## `serve`

Start the HTTP server. Loads the pipeline once and keeps it warm; uvicorn handles request multiplexing.

| Argument / option | Type | Default | Description |
|---|---|---|---|
| `pipeline` | string | required | Pipeline import path in `module:variable` format (e.g. `pipeline:pipeline`). |
| `--host` | string | `127.0.0.1` | Bind host. Set to `0.0.0.0` to expose on all interfaces. |
| `--port` | int | `8000` | Bind port. |
| `--log-level` | string | `info` | Log level. One of `debug`, `info`, `warning`, `error`. |

The pipeline path is forwarded to a uvicorn factory via the `PIIGHOST_PIPELINE` env var, so the server can hot-reload without rebuilding the import path.

```bash
piighost-api serve pipeline:pipeline --host 0.0.0.0 --port 8000
```

---

## `dataset extract`

Pull HITL and / or non-HITL traces from the configured observation backend (Langfuse) into a JSONL training file. Requires the `dataset` extra (`uv add piighost-api[dataset]`).

The command auto-loads a `.env` from the working directory if `python-dotenv` is available, so `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` can live there instead of being exported manually.

| Option | Type | Default | Description |
|---|---|---|---|
| `--output` / `-o` | path | required | Destination JSONL file. |
| `--since` | datetime | unset | ISO timestamp; skip traces older than this. |
| `--until` | datetime | unset | ISO timestamp; skip traces newer than this. |
| `--mode` | enum | `all` | `all`, `hitl`, or `model-only`. |
| `--limit` | int | unset | Stop after N records. |

**JSONL record schema**

```json
{
  "text": "Bonjour Patrick, comment vas tu ?",
  "entities": [[8, 15, "PERSON"]],
  "model_entities": [[8, 15, "ORG"]],
  "labels_universe": ["PERSON", "LOCATION"],
  "source": "hitl",
  "trace_id": "abc...",
  "session_id": "u1",
  "created_at": "2026-05-01T05:47:27.000Z"
}
```

- `entities` is the ground truth (human corrections in `hitl` records, model output in `model-only` records).
- `model_entities` is always the model's prediction; matches `entities` for `model-only` records.
- `labels_universe` is the detector's vocabulary at correction time when the detector exposes `.labels`, empty otherwise.
- `source` is `"hitl"` for HITL traces, `"model"` for non-HITL traces.

**Mode semantics**

| `--mode` | Trace name | `entities` source |
|---|---|---|
| `hitl` | `piighost.hitl_correction` | `output.detections` (human) |
| `model-only` | `piighost.anonymize_pipeline` | child `piighost.detect` `output.detections` |
| `all` (default) | both | per-trace |

**Example**

```bash
piighost-api dataset extract --output /tmp/dataset.jsonl --since 2026-04-01 --limit 1000
```

---

## `dataset metrics`

Compute per-label precision / recall / F1 from a JSONL produced by `dataset extract`. Pure stdlib; no extra installs needed.

| Option | Type | Default | Description |
|---|---|---|---|
| `--input` / `-i` | path | required | JSONL file to read. |
| `--output` / `-o` | path | unset | Write the report to this path instead of stdout. |
| `--output-format` | enum | `table` | `table`, `csv`, or `json`. |
| `--match-mode` | enum | `strict` | `strict` (exact span+label) or `lenient` (IoU ≥ `--iou-threshold`). |
| `--iou-threshold` | float | `0.5` | IoU floor in lenient mode. |
| `--source` | enum | `all` | `all`, `hitl`, or `model`; restrict aggregation to one source. |

**Output columns**

| Column | Meaning |
|---|---|
| `tp` | True positive (model and human agreed). |
| `fp` | False positive (model predicted, human deleted or relabelled). |
| `fn` | False negative (human added, model missed). |
| `P` | Precision = `tp / (tp + fp)`. |
| `R` | Recall = `tp / (tp + fn)`. |
| `F1` | Harmonic mean of P and R. |

The table also reports macro and micro averages and, when label-level confusion exists (same span, different labels), a confusion section.

**Example**

```bash
piighost-api dataset metrics --input /tmp/dataset.jsonl --source hitl
```

```text
label                    tp     fp     fn      P      R     F1
--------------------------------------------------------------
PERSON                    3      0      1   1.00   0.75   0.86
LOCATION                  2      0      1   1.00   0.67   0.80
--------------------------------------------------------------
macro avg                 -      -      -   1.00   0.71   0.83
micro avg                 -      -      -   1.00   0.71   0.83
```

---

## Typical workflow

```bash
# 1. Extract the last week of HITL corrections
piighost-api dataset extract --output /tmp/last_week.jsonl --since "$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S)"

# 2. Inspect the dataset before training
piighost-api dataset metrics --input /tmp/last_week.jsonl --source hitl

# 3. Convert to spaCy / GLiNER / your training tooling (out of scope of this CLI)
```
