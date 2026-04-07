#!/bin/sh
set -e

# Install extra packages at runtime (for pre-built images).
# Usage: EXTRA_PACKAGES="spacy faker" docker compose up
if [ -n "$EXTRA_PACKAGES" ]; then
    echo "Installing extra packages: $EXTRA_PACKAGES"
    /app/.venv/bin/pip install --no-cache-dir $EXTRA_PACKAGES
fi

exec "$@"