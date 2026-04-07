#!/bin/sh
set -e

# Install extra packages at runtime (for pre-built images).
# Usage: EXTRA_PACKAGES="spacy faker" docker compose up
if [ -n "$EXTRA_PACKAGES" ]; then
    echo "Installing extra packages: $EXTRA_PACKAGES"
    uv pip install --no-cache-dir --python /app/.venv/bin/python $EXTRA_PACKAGES
fi

exec "$@"