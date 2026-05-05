# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

piighost-api is a REST API server for [piighost](https://github.com/Athroniaeth/piighost) PII anonymization inference. It wraps a `ThreadAnonymizationPipeline` behind Litestar HTTP endpoints, with keyshield API key auth and Redis caching.

## Development Commands

```bash
make install                 # uv sync (PyPI: default install path)
make dev-local               # uv sync + uv pip install -e ../piighost (editable lib)
make lint                    # Format (ruff), lint (ruff), type-check (pyrefly)
make test                    # Run all tests
make docker-up               # Start Docker services (API + Redis)
make docker-down             # Stop Docker services
make hooks                   # Install the prek pre-commit hook (run once per clone)
```

## Install workflow (PyPI default, dev-local opt-in)

The committed `pyproject.toml` resolves `piighost` from PyPI; the lockfile records `source = { registry = "https://pypi.org/simple" }`. CI, Docker, and fresh clones all use the standard path with no flags.

When iterating on the sibling `~/PycharmProjects/piighost` lib **without** publishing a release:

1. `make install` once (or after pulling) to install the published baseline.
2. `make dev-local` to layer an editable install of `../piighost` on top. From this point on, source changes in the lib are picked up live by this server.
3. **Caveat**: any subsequent `uv sync` or `uv run` (which auto-syncs from the lockfile) reinstalls piighost from PyPI and undoes the editable. Re-run `make dev-local` if that happens. Set `UV_NO_SYNC=1` in your shell to silence the auto-sync if you want a stickier setup.

The `prek` hook installed via `make hooks` runs `uv lock --locked --no-sources` on every commit touching `uv.lock` or `pyproject.toml`. It blocks commits whose lockfile records `piighost` as a `file://` editable source — defense-in-depth against accidentally shipping a dev-mode lockfile.

There is no need to `cz bump` and publish to PyPI just to test changes against this server. Bumping is reserved for actual external releases.

## Architecture

### Application Factory (`app.py`)

`create_app(pipeline_path)` builds a Litestar app:
1. Loads the pipeline via `load_pipeline()` (module:variable pattern)
2. Initializes keyshield `ApiKeyService` with Argon2 hasher
3. On startup (lifespan): loads API keys from env, enables auth guard if keys valid
4. Registers route handlers as closures over the pipeline instance

### Endpoints

- `GET /v1/config` — pipeline labels and placeholder factory type
- `POST /v1/anonymize` — full NER detection + anonymization, returns entities
- `POST /v1/deanonymize` — cache-based deanonymization (404 on cache miss)
- `POST /v1/deanonymize/entities` — entity-based token replacement (for LLM responses)

### Request/Response Validation

All request/response schemas are `msgspec.Struct` classes in `app.py`. Litestar handles JSON serialization/deserialization automatically.

### Authentication (`auth.py`)

Litestar guard using `keyshield.ApiKeyService.verify_key()`. Expects `Authorization: Bearer <key>` header. If no valid API keys are loaded at startup, auth is disabled (development mode).

### Pipeline Loading (`loader.py`)

`load_pipeline("module:variable")` imports a Python module and extracts the named variable. Validates it's a `ThreadAnonymizationPipeline` instance. Adds CWD to `sys.path` like uvicorn.

### CLI (`cli.py`)

`piighost-api serve myconfig:pipeline --host --port --log-level` — passes the pipeline path to `create_app()` via environment variable, runs uvicorn with factory mode.

## Conventions

- **Commits**: Conventional Commits via Commitizen (`feat:`, `fix:`, `refactor:`, etc.)
- **Type checking**: PyReFly (not mypy)
- **Formatting/linting**: Ruff
- **Package manager**: uv (not pip)
- **Python**: 3.12+
- **Request/response models**: msgspec Struct (not pydantic, not dataclasses)
- **Tests**: 100% coverage target, mock pipeline (no real NER model in tests)
