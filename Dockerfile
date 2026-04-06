FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy project metadata and install dependencies
COPY pyproject.toml uv.lock* README.md ./

# Install dependencies (torch resolves to CPU-only via tool.uv.sources)
RUN uv sync --frozen --no-dev --no-progress || uv sync --no-dev --no-progress

# Copy source code and install the project itself
COPY src src
RUN uv sync --frozen --no-dev --no-progress

# Configurable via environment variables
ENV PIPELINE_PATH=pipeline:pipeline
ENV API_HOST=0.0.0.0
ENV API_PORT=8000
ENV LOG_LEVEL=info

EXPOSE ${API_PORT}

# Run directly from venv (no uv run, avoids re-sync at startup)
CMD ["sh", "-c", "/app/.venv/bin/piighost-api serve $PIPELINE_PATH --host $API_HOST --port $API_PORT --log-level $LOG_LEVEL"]
