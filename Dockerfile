FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy project metadata and install dependencies
COPY pyproject.toml uv.lock* README.md ./

# Install dependencies from the lockfile. The committed lockfile is
# kept in PyPI mode (the prek hook in .pre-commit-config.yaml blocks
# editable-source lockfiles from landing on master), so --frozen alone
# already pulls piighost from PyPI inside the image. The fallback path
# is for the rare case the lockfile is missing or out of sync; it
# re-resolves from indexes and uses --no-sources to bypass the local
# ../piighost path override declared in pyproject.toml. uv rejects
# --frozen with --no-sources, so the two flags only coexist on the
# fallback branch.
RUN uv sync --frozen --no-dev --no-progress || uv sync --no-dev --no-progress --no-sources

# Copy source code and install the project itself
COPY src src
RUN uv sync --frozen --no-dev --no-progress

# Optional: install extra piighost extras at build time
# Usage: docker build --build-arg PIIGHOST_EXTRAS="gliner2,faker" .
ARG PIIGHOST_EXTRAS=""
RUN if [ -n "$PIIGHOST_EXTRAS" ]; then \
        /app/.venv/bin/pip install --no-cache-dir "piighost[$PIIGHOST_EXTRAS]"; \
    fi

# Entrypoint installs EXTRA_PACKAGES at runtime (for pre-built images)
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Configurable via environment variables
ENV PIPELINE_PATH=pipeline:pipeline
ENV API_HOST=0.0.0.0
ENV API_PORT=8000
ENV LOG_LEVEL=info

EXPOSE ${API_PORT}

# Run directly from venv (no uv run, avoids re-sync at startup)
CMD ["sh", "-c", "/app/.venv/bin/piighost-api serve $PIPELINE_PATH --host $API_HOST --port $API_PORT --log-level $LOG_LEVEL"]
