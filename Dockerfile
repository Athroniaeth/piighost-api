# Two stages. The build stage resolves the locked dependencies with uv, the
# runtime stage keeps only /app, without uv and without its cache.
ARG UV_VERSION=0.12.23

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
# No cache in the layer, real files rather than links into it, and the
# image's Python rather than a downloaded one.
ENV UV_NO_CACHE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# A model extra takes the CPU build of PyTorch when no NVIDIA GPU is found,
# 1 GB instead of 5.4 GB.
ENV UV_TORCH_BACKEND=auto
WORKDIR /app

# Dependencies first, so a change in src does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src src
RUN uv sync --frozen --no-dev

# Optional: install piighost extras at build time.
# Usage: docker build --build-arg PIIGHOST_EXTRAS="gliner2" .
ARG PIIGHOST_EXTRAS=""
RUN if [ -n "$PIIGHOST_EXTRAS" ]; then \
        uv pip install --python /app/.venv/bin/python "piighost[$PIIGHOST_EXTRAS]"; \
    fi

FROM python:3.12-slim
ARG UV_VERSION
# entrypoint.sh fetches this uv only when EXTRA_PACKAGES asks for packages.
ENV UV_VERSION=${UV_VERSION} UV_TORCH_BACKEND=auto
WORKDIR /app
COPY --from=build /app /app

# The default configuration, every regex group of the piighost hub. Mount
# another file over it, or set PIIGHOST_CONFIG to a hub reference.
COPY pipeline.toml ./

# Entrypoint installs EXTRA_PACKAGES at runtime (for pre-built images)
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Configurable via environment variables
ENV PIIGHOST_CONFIG=/app/pipeline.toml
ENV API_HOST=0.0.0.0
ENV API_PORT=8000
ENV LOG_LEVEL=info

EXPOSE ${API_PORT}

# Run directly from venv (no uv run, avoids re-sync at startup)
CMD ["sh", "-c", "/app/.venv/bin/piighost-api serve --config $PIIGHOST_CONFIG --host $API_HOST --port $API_PORT --log-level $LOG_LEVEL"]
