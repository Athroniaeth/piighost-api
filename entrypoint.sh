#!/bin/sh
set -e

# Install extra packages at runtime (for pre-built images).
# Usage: EXTRA_PACKAGES="spacy faker" docker compose up
#
# Cache: uv reuses its wheel cache at $UV_CACHE_DIR (default /root/.cache/uv).
# Mount a named volume on that path to avoid re-downloading multi-GB wheels
# (torch, nvidia-*, triton, ...) across container restarts. Set UV_NO_CACHE=1
# to opt out when disk pressure is a concern.
if [ -n "$EXTRA_PACKAGES" ]; then
    echo "Installing extra packages: $EXTRA_PACKAGES"
    uv pip install --python /app/.venv/bin/python $EXTRA_PACKAGES
fi

exec "$@"