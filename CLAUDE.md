# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

piighost-api is a REST API server for [piighost](https://github.com/Athroniaeth/piighost) PII anonymization inference. It wraps a `ThreadAnonymizationPipeline` behind Litestar HTTP endpoints, with keyshield API key auth and Redis caching.

## Development Commands

```bash
uv sync                      # Install dependencies
make lint                    # Format (ruff), lint (ruff), type-check (pyrefly)
make test                    # Run all tests
make docker-up               # Start Docker services (API + Redis)
make docker-down             # Stop Docker services
```

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
