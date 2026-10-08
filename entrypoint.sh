#!/bin/sh
set -e

# Install extra packages at runtime (for pre-built images).
# Usage: EXTRA_PACKAGES="piighost[gliner2]" docker compose up
#
# The image ships without uv. When EXTRA_PACKAGES is set, the pinned uv is
# fetched once into the uv cache directory, then installs the packages.
#
# Cache: uv reuses its wheel cache at $UV_CACHE_DIR (default /root/.cache/uv).
# Mount a named volume on that path to keep uv and the wheels (torch, ...)
# across container restarts. Set UV_NO_CACHE=1 to opt out when disk pressure
# is a concern.
#
# UV_TORCH_BACKEND=auto, set in the image, installs the CPU build of PyTorch
# when no NVIDIA GPU is visible. Set UV_TORCH_BACKEND=cu130, for instance, to
# force a CUDA build.
if [ -n "$EXTRA_PACKAGES" ]; then
    uv_home="${UV_CACHE_DIR:-/root/.cache/uv}/piighost-uv-${UV_VERSION}"
    if [ ! -x "$uv_home/bin/uv" ]; then
        echo "Fetching uv ${UV_VERSION}"
        python -m pip install --quiet --no-cache-dir --disable-pip-version-check \
            --target "$uv_home" "uv==${UV_VERSION}"
    fi
    echo "Installing extra packages: $EXTRA_PACKAGES"
    "$uv_home/bin/uv" pip install --python /app/.venv/bin/python $EXTRA_PACKAGES
fi

exec "$@"
