## 0.1.0 (2026-03-30)

### Feat

- **api**: Litestar REST server with anonymize/deanonymize/config endpoints
- **auth**: keyshield API key authentication via Litestar guards
- **loader**: dynamic pipeline loading (`module:variable` pattern like uvicorn)
- **cli**: `piighost-api serve` command with host/port/log-level options
- **docker**: Dockerfile + docker-compose with Redis cache
- **tests**: full e2e test suite with mocks (100% coverage)
