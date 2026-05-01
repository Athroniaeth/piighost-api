---
icon: lucide/zap
---

# Quickstart

Spin up a piighost-api server, run your first anonymization request, see the placeholder in action. Five minutes from a fresh repo clone.

## 1. Write a `pipeline.py`

The server loads a single pipeline at boot, specified via `module:variable`. Create `pipeline.py` next to the place you'll run the server from. Regex-only is enough to play:

```python
from piighost.anonymizer import Anonymizer
from piighost.detector import RegexDetector
from piighost.linker.entity import ExactEntityLinker
from piighost.pipeline.thread import ThreadAnonymizationPipeline
from piighost.placeholder import LabelCounterPlaceholderFactory
from piighost.resolver.entity import MergeEntityConflictResolver
from piighost.resolver.span import ConfidenceSpanConflictResolver

pipeline = ThreadAnonymizationPipeline(
    detector=RegexDetector(
        patterns={
            "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            "PHONE": r"\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{1,4}){1,4}",
        }
    ),
    span_resolver=ConfidenceSpanConflictResolver(),
    entity_linker=ExactEntityLinker(),
    entity_resolver=MergeEntityConflictResolver(),
    anonymizer=Anonymizer(LabelCounterPlaceholderFactory()),
)
```

## 2. Start the server

```bash
piighost-api serve pipeline:pipeline --host 0.0.0.0 --port 8000
```

Expected log: `Pipeline ready: RegexDetector` and uvicorn listening on `0.0.0.0:8000`.

## 3. First request

```bash
curl -X POST http://localhost:8000/v1/anonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email me at patrick@acme.com", "thread_id": "demo"}'
```

Response:

```json
{
  "anonymized_text": "Email me at <<EMAIL:1>>",
  "entities": [
    {
      "label": "EMAIL",
      "placeholder": "<<EMAIL:1>>",
      "detections": [{"text": "patrick@acme.com", "label": "EMAIL", "start_pos": 12, "end_pos": 28, "confidence": 1.0}]
    }
  ]
}
```

## 4. Round-trip

Pass the anonymized text back through `/v1/deanonymize` (cached path) to recover the original:

```bash
curl -X POST http://localhost:8000/v1/deanonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email me at <<EMAIL:1>>", "thread_id": "demo"}'
```

Response: the original `Email me at patrick@acme.com`.

## 5. Optional: observation

Set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (or `OPIK_API_KEY`) in your environment before starting the server. Each anonymize call then emits a trace tree (`piighost.anonymize_pipeline` → `detect` → `link` → `placeholder` → `guard`). See [REST endpoints](../reference/endpoints.md) for the per-endpoint behaviour.

## Container path

If you prefer Docker, the [Installation](installation.md) page documents the GHCR image. The same `pipeline.py` mounts in via a volume.

## Next

- [REST endpoints](../reference/endpoints.md) — every endpoint, with request and response shapes.
- [CLI](../reference/cli.md) — server flags plus the `dataset extract|metrics` subcommands.
