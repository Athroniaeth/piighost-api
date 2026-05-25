# piighost-api TOML configuration migration plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch `piighost-api` from the Python `module:variable` loader to the declarative TOML loader provided by `piighost.config`. Add a `/v1/labels` route. Update Dockerfile, docker-compose, tests, and docs accordingly.

**Architecture:** `piighost-api serve` accepts `--config <path.toml>` (with `PIIGHOST_CONFIG` env fallback). `create_app` consumes `piighost.config.load_pipeline` which returns both the pipeline and a `PipelineManifest`. The manifest is captured in a closure by the new `/v1/labels` route. The old `/v1/config` route is removed.

**Tech Stack:** Python 3.12+, Litestar, msgspec, Typer, piighost.config (Pydantic v2 + tomllib).

**Scope:** This plan covers `piighost-api` only. A follow-up plan in `piighost-chat` will create `pipeline.toml`, update `compose.infra.yml`, and document the migration.

**Reference spec:** `/home/secondary/PycharmProjects/piighost/docs/superpowers/specs/2026-05-25-toml-pipeline-config-design.md` (sections "piighost-api changes" and "/v1/labels route").

---

## File structure

```
src/piighost_api/cli.py        # serve signature: --config, env fallback
src/piighost_api/app.py        # create_app(config_path: Path), /v1/labels closure, drop /v1/config
src/piighost_api/loader.py     # DELETED
tests/                          # update fixtures, add /v1/labels test, drop module:variable tests
Dockerfile                      # CMD uses --config; env var renamed PIPELINE_PATH -> PIIGHOST_CONFIG
docker-compose.yml              # mount pipeline.toml, set PIIGHOST_CONFIG
docs/en/getting-started/quickstart.md    # use --config <toml>
docs/en/reference/cli.md                 # serve signature changed
docs/en/reference/endpoints.md           # /v1/labels replaces /v1/config
docs/en/migration.md                     # NEW: migration guide from .py to .toml
pipeline.toml                   # NEW: example TOML at the repo root (replaces pipeline.py)
pipeline.py                     # DELETED
```

---

## Phase 1: CLI and app loader

### Task 1: Replace serve CLI signature

**Files:** `src/piighost_api/cli.py`

- [ ] **Step 1: Write the failing test (manual run; the existing CLI tests live in `tests/`)**

There's currently no test for `serve` itself (uvicorn factory). We'll add one inline:

Add to `tests/test_cli_serve.py` (NEW):

```python
"""Smoke tests for the `serve` subcommand argument parsing."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from piighost_api.cli import app


runner = CliRunner()


def test_serve_requires_config_flag_or_env(monkeypatch):
    monkeypatch.delenv("PIIGHOST_CONFIG", raising=False)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "config" in result.output.lower() or "config" in result.stderr.lower()


def test_serve_rejects_module_variable_format(monkeypatch, tmp_path):
    monkeypatch.delenv("PIIGHOST_CONFIG", raising=False)
    # The new CLI takes a file path, not a module:variable string.
    result = runner.invoke(app, ["serve", "--config", "pipeline:pipeline"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run test, expect failure**

`uv run pytest tests/test_cli_serve.py -v` should fail (serve still takes positional `pipeline`).

- [ ] **Step 3: Rewrite the `serve` command**

Edit `src/piighost_api/cli.py`. Replace the existing `@app.command() def serve(...)` block with:

```python
@app.command()
def serve(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a piighost TOML configuration file. "
        "Falls back to the PIIGHOST_CONFIG environment variable.",
    ),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    log_level: str = typer.Option(
        "info", help="Log level (debug | info | warning | error)."
    ),
) -> None:
    """Start the API server.

    The pipeline configuration is loaded from a TOML file. Pass it via
    ``--config <path.toml>`` or set ``PIIGHOST_CONFIG`` in the environment.
    The old ``module:variable`` Python loader has been removed; see the
    migration guide in the docs for the equivalent TOML of common
    Python pipelines.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = config or _config_from_env()
    if config is None:
        typer.echo(
            "Missing --config or PIIGHOST_CONFIG. "
            "Pass a TOML file path: piighost-api serve --config pipeline.toml",
            err=True,
        )
        raise typer.Exit(code=1)
    if not config.exists():
        typer.echo(f"Configuration file not found: {config}", err=True)
        raise typer.Exit(code=1)

    os.environ["PIIGHOST_CONFIG"] = str(config.resolve())
    uvicorn.run(
        "piighost_api.cli:_create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
    )


def _config_from_env() -> Path | None:
    raw = os.environ.get("PIIGHOST_CONFIG")
    if not raw:
        return None
    return Path(raw)
```

Update `_create_app` at the bottom of `cli.py`:

```python
def _create_app():
    """App factory called by uvicorn."""
    from piighost_api.app import create_app

    config_path = Path(os.environ["PIIGHOST_CONFIG"])
    return create_app(config_path)
```

- [ ] **Step 4: Run tests**

`uv run pytest tests/test_cli_serve.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/cli.py tests/test_cli_serve.py
git commit -m "feat(cli)!: replace module:variable pipeline loader with --config TOML

BREAKING CHANGE: piighost-api serve no longer accepts a module:variable
positional argument. Pass --config <path.toml> or set PIIGHOST_CONFIG
in the environment. See docs/migration.md for the equivalent TOML of
common Python pipelines."
```

---

### Task 2: Switch `create_app` to load_pipeline

**Files:** `src/piighost_api/app.py`, `src/piighost_api/loader.py` (DELETE)

- [ ] **Step 1: Inspect the existing `app.py`**

Read `src/piighost_api/app.py` end-to-end to understand the current closure structure (the `pipeline` variable captured by route handlers, the `_serialize_entities` helpers, the auth lifespan, etc.).

- [ ] **Step 2: Modify `create_app`**

Replace the existing function signature `def create_app(pipeline_path: str) -> Litestar:` with:

```python
def create_app(config_path: Path) -> Litestar:
    """Create and configure the Litestar application.

    Args:
        config_path: Path to a piighost TOML configuration file.

    Returns:
        A fully configured ``Litestar`` instance.
    """
    pipeline, manifest = load_pipeline(config_path)
    # ... rest of the function body unchanged, manifest captured in
    # closure for the new /v1/labels route added in Task 3.
```

At the top of the file, replace:

```python
from piighost_api.loader import load_pipeline
```

with:

```python
from piighost.config import load_pipeline
```

- [ ] **Step 3: Delete `loader.py`**

```bash
git rm src/piighost_api/loader.py
```

- [ ] **Step 4: Update the lifespan logger to use the manifest**

Find the existing line `logger.info("Pipeline ready: %s", type(pipeline._detector).__name__)` (or similar) and change it to:

```python
logger.info(
    "Pipeline ready: %s (%d detector(s))",
    manifest.name or "<unnamed>",
    len(manifest.detectors),
)
```

- [ ] **Step 5: Run existing test suite to confirm no regression**

`uv run pytest -v` — Some tests that depended on `load_pipeline(module:variable_str)` will fail. Note them; they'll be fixed in Task 4 (test fixture update).

- [ ] **Step 6: Commit**

```bash
git add src/piighost_api/app.py
git rm src/piighost_api/loader.py
git commit -m "feat(app)!: load pipeline via piighost.config TOML loader

create_app now takes a Path to a TOML file instead of a Python
import path. The piighost_api.loader module is removed; that
responsibility moved to piighost.config.load_pipeline."
```

---

## Phase 2: /v1/labels route

### Task 3: Replace /v1/config with /v1/labels

**Files:** `src/piighost_api/app.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_labels.py` (NEW):

```python
"""Tests for the /v1/labels route."""

from pathlib import Path

import pytest
from litestar.testing import TestClient

from piighost_api.app import create_app


FIXTURES = Path(__file__).parent / "fixtures"


def test_labels_returns_grouped_detector_labels():
    app = create_app(FIXTURES / "multi_detector.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/labels")
    assert response.status_code == 200
    body = response.json()
    assert "pipeline" in body
    assert body["pipeline"]["schema_version"] == 1
    assert "detectors" in body
    assert len(body["detectors"]) == 2
    # The fixture has a "common" regex detector and a "secondary" regex detector.
    names = [d["name"] for d in body["detectors"]]
    assert "common" in names
    assert "secondary" in names


def test_v1_config_route_is_removed():
    app = create_app(FIXTURES / "minimal.toml")
    with TestClient(app=app) as client:
        response = client.get("/v1/config")
    assert response.status_code == 404
```

You will also need to create two TOML fixtures at `tests/fixtures/`:

`tests/fixtures/minimal.toml`:
```toml
[[detectors]]
type = "regex"
patterns = { EMAIL = "[a-z]+@[a-z]+\\.[a-z]+" }
```

`tests/fixtures/multi_detector.toml`:
```toml
[pipeline]
name = "demo"

[[detectors]]
name = "common"
type = "regex"
patterns = { EMAIL = "[a-z]+@[a-z]+\\.[a-z]+" }

[[detectors]]
name = "secondary"
type = "regex"
patterns = { IP_V4 = "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b" }
```

- [ ] **Step 2: Run, expect failure**

`uv run pytest tests/test_routes_labels.py -v` should fail (route doesn't exist yet, `/v1/config` still exists).

- [ ] **Step 3: Add the new route and remove the old one**

In `src/piighost_api/app.py`:

1. Add new msgspec structs near the existing ones (after `class ConfigResponse(msgspec.Struct):`):

```python
class DetectorLabelsSchema(msgspec.Struct):
    name: str | None
    type: str
    labels: list[str]


class PipelineMetaSchema(msgspec.Struct):
    name: str | None
    schema_version: int


class LabelsResponse(msgspec.Struct):
    pipeline: PipelineMetaSchema
    detectors: list[DetectorLabelsSchema]
```

2. Delete the existing `ConfigResponse` struct, the `_get_detector_labels` helper function, and the `@get("/v1/config")` handler.

3. Add the new `@get("/v1/labels")` handler inside `create_app` (it needs to close over `manifest`):

```python
    @get("/v1/labels", exclude_from_auth=True)
    async def labels() -> LabelsResponse:
        return LabelsResponse(
            pipeline=PipelineMetaSchema(
                name=manifest.name,
                schema_version=manifest.schema_version,
            ),
            detectors=[
                DetectorLabelsSchema(name=d.name, type=d.type, labels=d.labels)
                for d in manifest.detectors
            ],
        )
```

4. In the `route_handlers=[...]` list at the bottom of `create_app`, replace `get_config` with `labels`.

- [ ] **Step 4: Run tests**

`uv run pytest tests/test_routes_labels.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/piighost_api/app.py tests/test_routes_labels.py tests/fixtures/
git commit -m "feat(api)!: replace /v1/config with /v1/labels

BREAKING CHANGE: the /v1/config route is removed. /v1/labels returns
labels grouped by detector along with pipeline metadata. Format:
{
  \"pipeline\": {\"name\": ..., \"schema_version\": 1},
  \"detectors\": [{\"name\": ..., \"type\": ..., \"labels\": [...]}, ...]
}"
```

---

## Phase 3: Test migration

### Task 4: Migrate existing test fixtures to TOML

**Files:** `tests/` (various)

- [ ] **Step 1: List existing test files that use `module:variable` or `pipeline.py`**

Run: `grep -rn 'module:variable\|pipeline:pipeline\|pipeline_path\|load_pipeline' tests/`

For each match, decide whether the test needs a TOML fixture or just a code update.

- [ ] **Step 2: Update each affected test**

For tests that called `create_app("pipeline:pipeline")` or similar, change to `create_app(fixtures / "minimal.toml")` (using the fixtures created in Task 3).

For tests that imported `from piighost_api.loader import load_pipeline`, change to `from piighost.config import load_pipeline` and pass a `Path` instead of a string.

If any test exercised the `module:variable` parser specifically (e.g. `test_load_pipeline_rejects_bad_format`), DELETE it — that loader no longer exists.

- [ ] **Step 3: Run the full suite**

`uv run pytest -v`
Expected: All PASS. No more references to the deleted loader.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: migrate fixtures from pipeline.py to pipeline.toml"
```

---

## Phase 4: Dockerfile + compose

### Task 5: Update Dockerfile to use TOML config

**Files:** `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`

- [ ] **Step 1: Rewrite the relevant Dockerfile lines**

Edit `Dockerfile`. Change:

```dockerfile
ENV PIPELINE_PATH=pipeline:pipeline
...
CMD ["sh", "-c", "/app/.venv/bin/piighost-api serve $PIPELINE_PATH --host $API_HOST --port $API_PORT --log-level $LOG_LEVEL"]
```

To:

```dockerfile
ENV PIIGHOST_CONFIG=/app/pipeline.toml
...
CMD ["sh", "-c", "/app/.venv/bin/piighost-api serve --config $PIIGHOST_CONFIG --host $API_HOST --port $API_PORT --log-level $LOG_LEVEL"]
```

- [ ] **Step 2: Update `docker-compose.yml`**

Edit `docker-compose.yml`:

```yaml
services:
  api:
    build: .
    ports:
      - "${API_PORT:-8000}:${API_PORT:-8000}"
    environment:
      - PIIGHOST_CONFIG=${PIIGHOST_CONFIG:-/app/pipeline.toml}
      - API_HOST=${API_HOST:-0.0.0.0}
      ...
    volumes:
      - ./pipeline.toml:/app/pipeline.toml
      ...
```

Replace `PIPELINE_PATH=...` with `PIIGHOST_CONFIG=...` and the volume `./pipeline.py:/app/pipeline.py` with `./pipeline.toml:/app/pipeline.toml`.

- [ ] **Step 3: Create `pipeline.toml` at the repo root**

Replace the current `pipeline.py` with `pipeline.toml`. The existing `pipeline.py` uses 3 RegexDetectors (common, EU, US). Translate:

```toml
# pipeline.toml — equivalent of the previous pipeline.py example.
[pipeline]
name = "piighost-api-default"
description = "Default regex-only PII coverage (common, EU, US)."
schema_version = 1

[[detectors]]
name = "common"
type = "regex"
patterns = { EMAIL = "[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}", IP_V4 = "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b", IP_V6 = "\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b", URL = "https?://[^\\s<>\"']+[^\\s<>\"'.,;:!?\\)\\]}]", CREDIT_CARD = "\\b\\d{4}[\\s\\-]\\d{4}[\\s\\-]\\d{4}[\\s\\-]\\d{4}\\b", PHONE_INTERNATIONAL = "\\+\\d{1,3}[\\s.\\-]?\\(?\\d{1,4}\\)?(?:[\\s.\\-]?\\d{1,4}){1,4}", OPENAI_API_KEY = "sk-(?:proj-)?[A-Za-z0-9\\-_]{20,}", AWS_ACCESS_KEY = "\\bAKIA[0-9A-Z]{16}\\b", GITHUB_TOKEN = "\\bgh[ps]_[A-Za-z0-9_]{36,}\\b", STRIPE_KEY = "\\b[sr]k_(?:live|test)_[A-Za-z0-9]{24,}\\b" }

[[detectors]]
name = "eu"
type = "regex"
patterns = { EU_IBAN = "\\b[A-Z]{2}\\d{2}[A-Z0-9]{4}\\d{7}[A-Z0-9]{0,16}\\b", EU_VAT = "\\b[A-Z]{2}\\d{8,12}\\b", FR_SSN = "\\b[12]\\d{2}(?:0[1-9]|1[0-2])\\d{2}\\d{3}\\d{3}\\d{2}(?:\\s?\\d{2})?\\b", FR_PHONE = "\\b(?:\\+33|0)[1-9](?:[\\s.\\-]?\\d{2}){4}\\b", FR_ZIP = "\\b(?:0[1-9]|[1-8]\\d|9[0-8])\\d{3}\\b", DE_PHONE = "\\b(?:\\+49|0)\\d{2,5}[\\s/\\-]?\\d{3,10}\\b", DE_ZIP = "\\b(?:0[1-9]|[1-9]\\d)\\d{3}\\b", UK_NINO = "\\b[A-CEGHJ-PR-TW-Z]{2}\\d{6}[A-D]\\b", UK_NHS = "\\b\\d{3}[\\s\\-]?\\d{3}[\\s\\-]?\\d{4}\\b", UK_POSTCODE = "\\b[A-Z]{1,2}\\d[A-Z\\d]?\\s?\\d[A-Z]{2}\\b" }

[[detectors]]
name = "us"
type = "regex"
patterns = { US_SSN = "\\b(?!000|666|9\\d{2})\\d{3}-(?!00)\\d{2}-(?!0000)\\d{4}\\b", US_PHONE = "\\b(?:\\+1[\\s.\\-]?)?\\(?\\d{3}\\)?[\\s.\\-]?\\d{3}[\\s.\\-]?\\d{4}\\b", US_PASSPORT = "\\b[A-Z]\\d{8}\\b", US_ZIP_CODE = "\\b\\d{5}(?:-\\d{4})?\\b", US_EIN = "\\b\\d{2}-\\d{7}\\b", US_BANK_ROUTING = "\\b\\d{9}\\b" }
```

Note: TOML inline-table syntax does not allow multi-line patterns. If a regex contains a quote character or a newline, you must use the `[detectors.patterns]` table form instead. For this fixture all patterns fit on one line each.

- [ ] **Step 4: Delete the old `pipeline.py`**

```bash
git rm pipeline.py
```

- [ ] **Step 5: Validate the new `pipeline.toml`**

`uv run piighost validate pipeline.toml`
Expected: `OK: pipeline.toml`.

(Requires `piighost[config]` installed; the local-dev `make dev-local` workflow already pulls it.)

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml pipeline.toml
git rm pipeline.py
git commit -m "build(docker): use --config pipeline.toml instead of module:variable

The container's CMD now passes --config to piighost-api serve. The env
var renamed from PIPELINE_PATH to PIIGHOST_CONFIG. The repo's example
pipeline switches from pipeline.py to pipeline.toml (regex-only)."
```

---

## Phase 5: Documentation

### Task 6: Update docs (en + fr)

**Files:** `docs/en/getting-started/quickstart.md`, `docs/en/reference/cli.md`, `docs/en/reference/endpoints.md`, `docs/en/migration.md` (NEW), and matching `docs/fr/*` files.

- [ ] **Step 1: Quickstart**

Open `docs/en/getting-started/quickstart.md`. Find any reference to `pipeline:pipeline`, `module:variable`, or `pipeline.py` and replace with the `--config pipeline.toml` form. Add a minimal `pipeline.toml` example near the top:

```toml
[[detectors]]
type = "regex"
patterns = { EMAIL = "[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}" }
```

Then: `piighost-api serve --config pipeline.toml`.

- [ ] **Step 2: CLI reference**

Open `docs/en/reference/cli.md` (create if not present). Document the new `serve` signature:

```
piighost-api serve [--config <path.toml>] [--host HOST] [--port PORT] [--log-level LEVEL]

Required:
  --config / -c <path.toml>   Path to a piighost TOML configuration file.
                              Falls back to PIIGHOST_CONFIG env var.
```

Mention the breaking change from the previous `module:variable` form.

- [ ] **Step 3: Endpoints reference**

Open `docs/en/reference/endpoints.md`. Remove the `GET /v1/config` section, add `GET /v1/labels` with the new response schema:

```json
{
  "pipeline": {"name": "string|null", "schema_version": 1},
  "detectors": [
    {"name": "string|null", "type": "regex", "labels": ["EMAIL", "IP_V4"]}
  ]
}
```

- [ ] **Step 4: Migration guide (NEW)**

Create `docs/en/migration.md`:

```markdown
# Migrating from Python pipeline files to TOML

`piighost-api` no longer accepts a `module:variable` Python import path
for the pipeline configuration. The new format is a declarative TOML
file consumed by `piighost.config.load_pipeline`. This page shows the
TOML equivalent of common Python pipelines.

## Single regex detector

Before (`pipeline.py`):

```python
from piighost.detector import RegexDetector
from piighost.pipeline.thread import ThreadAnonymizationPipeline
# ... other imports ...

detector = RegexDetector(patterns={"EMAIL": r"[a-z]+@[a-z]+\.[a-z]+"})
# ... assemble the rest of the pipeline ...
pipeline = ThreadAnonymizationPipeline(...)
```

Launch: `piighost-api serve pipeline:pipeline`

After (`pipeline.toml`):

```toml
[[detectors]]
type = "regex"
patterns = { EMAIL = "[a-z]+@[a-z]+\\.[a-z]+" }
```

Launch: `piighost-api serve --config pipeline.toml`

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
patterns = { EMAIL = "..." }
```

The `CompositeDetector` is created implicitly when more than one `[[detectors]]` entry is declared.

## Endpoint changes

`GET /v1/config` has been removed. Use `GET /v1/labels` for the equivalent (and richer) information; see the endpoints reference.

## Validating your TOML

```bash
piighost validate pipeline.toml
```

Exit code 0 on success, 1 on error with a path-prefixed message.
```

- [ ] **Step 5: French parity**

For each English doc touched, update the French sibling (`docs/fr/...`). Translate prose, keep code blocks identical. Respect house style: no em dashes, no mid-sentence colons.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: update quickstart, CLI, endpoints, add migration guide"
```

---

## Phase 6: Verification

### Task 7: Full-suite check + smoke test

- [ ] **Step 1: Run the full test suite**

`uv run pytest -v`
Expected: All PASS.

- [ ] **Step 2: Lint and type-check**

`make lint`
Expected: No errors.

- [ ] **Step 3: Smoke test the new CLI**

```bash
uv run piighost-api serve --config pipeline.toml --host 127.0.0.1 --port 8123 &
SERVER_PID=$!
sleep 4
curl -s http://127.0.0.1:8123/health
curl -s http://127.0.0.1:8123/v1/labels
curl -s -X POST http://127.0.0.1:8123/v1/anonymize \
  -H "Content-Type: application/json" \
  -d '{"text":"Contact alice@example.com from 192.168.1.1","thread_id":"smoke"}'
kill $SERVER_PID
```

Expected:
- `/health` returns `{"status":"ok","detector":"CompositeDetector"}`
- `/v1/labels` returns grouped detector labels
- `/v1/anonymize` produces `<<EMAIL_1>>` and `<<IP_V4_1>>` tokens

- [ ] **Step 4: Confirm `/v1/config` returns 404**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8123/v1/config
```

Expected: `404`.

- [ ] **Step 5: Final commit (if cleanup edits needed)**

If lint produced reformat changes:

```bash
git add -p
git commit -m "style: ruff format pass"
```

Otherwise nothing to commit.

---

## Self-review checklist

- [ ] No reference to `module:variable`, `PIPELINE_PATH`, or `pipeline:pipeline` remains in source, tests, Docker, or docs.
- [ ] `pipeline.py` deleted.
- [ ] `pipeline.toml` validates with `piighost validate`.
- [ ] `/v1/config` returns 404.
- [ ] `/v1/labels` returns grouped detectors.
- [ ] French and English docs both updated.
- [ ] All commits use Conventional Commits, the breaking ones marked with `!`.
