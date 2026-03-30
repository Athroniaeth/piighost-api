FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy project metadata and install dependencies
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --frozen --no-dev --no-progress || uv sync --no-dev --no-progress

# Copy source code
COPY src src

EXPOSE 8000

# Default: load pipeline.py from /app (mount via volume or COPY)
CMD ["uv", "run", "piighost-api", "serve", "pipeline:pipeline", "--host", "0.0.0.0", "--port", "8000"]
