---
icon: lucide/server
---

# REST endpoints

All endpoints are mounted under the API root and accept JSON bodies (msgspec). When API keys are configured (`API_KEY_*` env vars at server boot), every endpoint except `GET /` and `GET /health` requires the configured header.

The OpenAPI / Swagger schema is also served live at `/schema/swagger`.

---

## `GET /`

Index. Returns the project name, version, and a pointer to the Swagger doc. No auth required.

```bash
curl http://localhost:8000/
```

```json
{"name": "piighost-api", "version": "0.6.0", "docs": "/schema/swagger"}
```

---

## `GET /health`

Liveness probe. Returns server status and the loaded detector class name. No auth required.

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "detector": "CompositeDetector"}
```

---

## `GET /v1/labels`

Returns the pipeline metadata and the labels grouped by detector. Use this endpoint in clients that need to know what entity types the pipeline can produce (e.g. to populate a label picker in a UI). Auth is not required.

```bash
curl http://localhost:8000/v1/labels
```

```json
{
  "pipeline": {"name": null, "schema_version": 1},
  "detectors": [
    {"name": null, "type": "regex", "labels": ["EMAIL", "PHONE"]}
  ]
}
```

---

## `POST /v1/detect`

Run the model-only detection (no anonymisation). Returns the entities the pipeline would have replaced. Side effect: populates the detection cache for `(text, thread_id)` so a subsequent `POST /v1/anonymize` on the same text does not re-run the detector.

**Request body** (`DetectRequest`)

| Field | Type | Default |
|---|---|---|
| `text` | string | required |
| `thread_id` | string | `"default"` |

**Response** (`DetectResponse`)

| Field | Type | Description |
|---|---|---|
| `entities` | list | Entities with their detections (no placeholders). |

```bash
curl -X POST http://localhost:8000/v1/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "Email patrick@acme.com", "thread_id": "u1"}'
```

---

## `PUT /v1/detect`

HITL override of the detection cache. Replaces the model's detections for `(text, thread_id)` with the user-supplied list, and invalidates the anonymise-result cache so the next `POST /v1/anonymize` re-runs with the corrected detections.

When observation is configured, this also emits a `piighost.hitl_correction` trace carrying the model and human detections; see the `dataset extract` CLI for using these traces as a NER training set.

**Request body** (`OverrideDetectRequest`)

| Field | Type | Default |
|---|---|---|
| `text` | string | required |
| `detections` | list | required (each: `{text, label, start_pos, end_pos, confidence}`) |
| `thread_id` | string | `"default"` |

**Response** — empty 200.

```bash
curl -X PUT http://localhost:8000/v1/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "Hi Alice", "thread_id": "u1", "detections": [{"text":"Alice","label":"PERSON","start_pos":3,"end_pos":8,"confidence":1.0}]}'
```

---

## `POST /v1/anonymize`

Run the full pipeline (detect → resolve spans → link → resolve entities → anonymize). Returns the anonymised text and the entity tree.

**Request body** (`AnonymizeRequest`): `{text, thread_id}` (same shape as `/v1/detect`).

**Response** (`AnonymizeResponse`)

| Field | Type | Description |
|---|---|---|
| `anonymized_text` | string | The text with PII replaced by placeholders. |
| `entities` | list | One entity per linked group: `{label, placeholder, detections}`. |

```bash
curl -X POST http://localhost:8000/v1/anonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email patrick@acme.com", "thread_id": "u1"}'
```

```json
{
  "anonymized_text": "Email <<EMAIL:1>>",
  "entities": [{"label": "EMAIL", "placeholder": "<<EMAIL:1>>", "detections": [...]}]
}
```

---

## `POST /v1/deanonymize`

Cached path. Looks up the previously-stored mapping for `(anonymised_text, thread_id)`; returns the original text. Errors with 404 when the mapping has expired or never existed.

**Request body** (`DeanonymizeRequest`): `{text, thread_id}`.

**Response** (`DeanonymizeResponse`): `{text, entities}` (the entities used for the original anonymise call).

```bash
curl -X POST http://localhost:8000/v1/deanonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email <<EMAIL:1>>", "thread_id": "u1"}'
```

---

## `POST /v1/deanonymize/entities`

Token-replacement path. Replaces every known token in *text* with its original value, in a single regex pass, using the thread's accumulated entity memory. Works on text the pipeline never anonymised (e.g. an LLM-generated reply that includes placeholders), unlike the cached path above.

**Request body** (`DeanonymizeRequest`): `{text, thread_id}`.

**Response** (`DeanonymizeEntResponse`): `{text}`.

```bash
curl -X POST http://localhost:8000/v1/deanonymize/entities \
  -H "Content-Type: application/json" \
  -d '{"text": "Hi <<PERSON:1>>!", "thread_id": "u1"}'
```

---

## Authentication

When `API_KEY_<NAME>=<key>` env vars are set at server boot, every protected endpoint requires the matching key in an `Authorization` header. See [keyshield](https://github.com/Athroniaeth/keyshield) for the details of scopes, rotation, and Argon2 hashing.

`GET /`, `GET /health`, and `GET /v1/labels` are always excluded from auth regardless of configuration.

When no API keys are configured, auth is disabled (the server logs `auth disabled` at startup).
