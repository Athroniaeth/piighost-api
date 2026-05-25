---
icon: lucide/arrow-right-left
---

# Migrating from Python pipeline files to TOML

`piighost-api` no longer accepts a `module:variable` Python import path for the pipeline configuration. The new format is a declarative TOML file consumed by `piighost.config.load_pipeline`. This page shows the TOML equivalent of common Python pipelines.

---

## Single regex detector

Before (`pipeline.py`):

```python
from piighost.detector import RegexDetector
# ... other imports ...

detector = RegexDetector(patterns={"EMAIL": r"[a-z]+@[a-z]+\.[a-z]+"})
# ... assemble the rest of the pipeline ...
```

Launch: `piighost-api serve pipeline:pipeline`

After (`pipeline.toml`):

```toml
[[detectors]]
type = "regex"

[detectors.patterns]
EMAIL = "[a-z]+@[a-z]+\\.[a-z]+"
```

Launch: `piighost-api serve --config pipeline.toml`

---

## GLiNER2 + regex composite

Before:

```python
model = GLiNER2.from_pretrained("fastino/gliner2-multi-v1")
gliner = Gliner2Detector(model=model, threshold=0.5, labels=["person", "city"])
common = RegexDetector(patterns={"EMAIL": "..."})
detector = CompositeDetector(detectors=[gliner, common])
```

After:

```toml
[[detectors]]
type = "gliner2"
model = "fastino/gliner2-multi-v1"
threshold = 0.5
labels = ["person", "city"]

[[detectors]]
type = "regex"

[detectors.patterns]
EMAIL = "..."
```

The `CompositeDetector` is created implicitly when more than one `[[detectors]]` entry is declared.

---

## Environment variable

The `PIPELINE_PATH` env var has been renamed to `PIIGHOST_CONFIG`. Set it to the path of your TOML file, or pass `--config <path>` to the CLI directly.

```bash
# Before
export PIPELINE_PATH=/app/pipeline.py

# After
export PIIGHOST_CONFIG=/app/pipeline.toml
```

---

## Endpoint changes

`GET /v1/config` has been removed. Use `GET /v1/labels` for the equivalent (and richer) information. See the [endpoints reference](reference/endpoints.md).

---

## Validating your TOML

```bash
piighost validate pipeline.toml
```

Exit code 0 on success, 1 on error with a path-prefixed message.

---

## Docker

If you maintain a `docker-compose.yml` referencing this image, update:

- `PIPELINE_PATH` → `PIIGHOST_CONFIG` (env var rename)
- `./pipeline.py:/app/pipeline.py` → `./pipeline.toml:/app/pipeline.toml` (volume mount)

Example diff:

```yaml
# Before
environment:
  - PIPELINE_PATH=/app/pipeline.py
volumes:
  - ./pipeline.py:/app/pipeline.py

# After
environment:
  - PIIGHOST_CONFIG=/app/pipeline.toml
volumes:
  - ./pipeline.toml:/app/pipeline.toml
```
