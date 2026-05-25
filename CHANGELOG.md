## 0.8.0 (2026-05-25)

### BREAKING CHANGE

- piighost-api serve no longer accepts a module:variable
positional argument. Pass --config <path.toml> or set PIIGHOST_CONFIG
in the environment. The /v1/config route is replaced by /v1/labels
with per-detector grouping. The piighost_api.loader module is removed
(responsibility moved to piighost.config.load_pipeline). pipeline.py
is replaced by pipeline.toml. PIPELINE_PATH env var renamed to
PIIGHOST_CONFIG.
- the /v1/config route is removed. /v1/labels returns
labels grouped by detector along with pipeline metadata.
- `piighost-api serve` no longer accepts a positional
`module:variable` argument. Pass a TOML config file via
`--config <path.toml>` or the `PIIGHOST_CONFIG` environment variable.
`_create_app` now reads `PIIGHOST_CONFIG` (Path) instead of
`PIIGHOST_PIPELINE` (module:variable string).
- ``make install-pypi`` is removed; use ``make install``
for the PyPI flow (which is now the default) or ``make dev-local`` for
the editable flow.

### Feat

- **api**: switch to piighost.config TOML loader
- **api**: replace /v1/config with /v1/labels
- **app**: load pipeline via piighost.config TOML loader
- **cli**: replace module:variable pipeline loader with --config TOML

### Fix

- **make**: make dev-local target the project venv and pull all extras
- **tests**: strip ANSI codes before asserting CLI help output
- **ci**: pass --no-sources to uv sync so CI ignores the local-dev path
- **detect**: propagate thread_id via _current_thread_id ContextVar

## 0.7.0 (2026-05-04)

### Feat

- **cli**: auto-load .env in dataset extract via python-dotenv
- **cli**: migrate to Typer and add 'dataset extract|metrics' subcommands
- **dataset**: add JSONL -> per-label P/R/F1 metrics primitives
- **dataset**: add Langfuse trace -> JSONL record shaping
- **deps**: add typer base dep and dataset extra (langfuse SDK)

### Fix

- **tests**: isolate dataset_extract missing-creds test from repo .env
- **cli**: friendly error when 'dataset' extra is not installed
- **docker**: drop conflicting --frozen + --no-sources combo

## 0.6.0 (2026-04-30)

### Feat

- add optional Langfuse/Opik observation backends

## 0.5.3 (2026-04-26)

## 0.5.2 (2026-04-20)

### Fix

- allow uv wheel cache to persist across runtime installs

## 0.5.1 (2026-04-16)

## 0.5.0 (2026-04-16)

### Feat

- add POST /v1/detect and PUT /v1/detect routes for entity correction

### Fix

- reorder OverrideDetectRequest fields to satisfy msgspec ordering

## 0.4.1 (2026-04-10)

### Fix

- use uv pip install instead of pip in entrypoint

## 0.4.0 (2026-04-07)

### Feat

- remove gliner2/torch from base deps, add EXTRA_PACKAGES and PIIGHOST_EXTRAS support

## 0.3.3 (2026-04-07)

### Fix

- run venv binary directly in CMD to avoid uv re-sync at startup

## 0.3.2 (2026-03-31)

### Perf

- use cpu-only torch to reduce docker image from ~5 GB to ~1.1 GB

## 0.3.1 (2026-03-30)

### Fix

- guard optional dependency imports (aiocache, faker, langgraph)

## 0.3.0 (2026-03-30)

### Fix

- guard optional dependency imports (aiocache, faker, langgraph)

## 0.3.0 (2026-03-30)

### Feat

- configurable Docker setup via .env with documented keyshield auth

## 0.2.0 (2026-03-30)

### Feat

- configurable Docker setup via .env with documented keyshield auth
- add CompositeDetector with GLiNER2 semantic labels and regex patterns (EU+US)
- add index, health, OpenAPI docs routes and HuggingFace cache volume

### Fix

- resolve pyrefly type-checking errors in tests

## 0.1.0 (2026-03-30)

### Feat

- **api**: Litestar REST server with anonymize/deanonymize/config endpoints
- **auth**: keyshield API key authentication via Litestar guards
- **loader**: dynamic pipeline loading (`module:variable` pattern like uvicorn)
- **cli**: `piighost-api serve` command with host/port/log-level options
- **docker**: Dockerfile + docker-compose with Redis cache
- **tests**: full e2e test suite with mocks (100% coverage)
