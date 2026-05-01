---
icon: lucide/download
---

# Installation

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended), pip, or Docker
- Optional: a Redis instance for shared cache, a Langfuse / Opik account for observation traces

## Python install

=== "uv"

    ```bash
    uv add piighost-api
    ```

=== "pip"

    ```bash
    pip install piighost-api
    ```

The base install ships with regex detectors only. NER detectors come from the `piighost` library extras (e.g. `piighost[gliner2]`).

## Optional extras

`piighost-api` exposes three optional extras that pull in observation or dataset tooling:

=== "uv"

    ```bash
    uv add piighost-api[langfuse]   # observation traces to Langfuse
    uv add piighost-api[opik]       # observation traces to Opik
    uv add piighost-api[dataset]    # piighost-api dataset extract|metrics CLI
    ```

=== "pip"

    ```bash
    pip install piighost-api[langfuse]
    pip install piighost-api[opik]
    pip install piighost-api[dataset]
    ```

Extras compose: `piighost-api[langfuse,dataset]` enables observation and the dataset CLI in one go.

## Docker

A pre-built image is published to GitHub Container Registry:

```bash
docker pull ghcr.io/athroniaeth/piighost-api:latest
```

Mount your `pipeline.py` and override `EXTRA_PACKAGES` to install detector extras at boot:

```yaml
services:
  piighost-api:
    image: ghcr.io/athroniaeth/piighost-api:latest
    environment:
      - EXTRA_PACKAGES=piighost[gliner2,langfuse]
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
    volumes:
      - ./pipeline.py:/app/pipeline.py
```

The entrypoint runs `uv pip install $EXTRA_PACKAGES` at startup, so the same image serves regex-only and NER deployments.

## Verify

```bash
piighost-api --help
```

Expected: a Typer help banner with `serve` and `dataset` subcommands.

## Next

Continue with the [Quickstart](quickstart.md) to write a pipeline and make your first request.
