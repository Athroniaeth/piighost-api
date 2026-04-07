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
