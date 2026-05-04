.PHONY: lint test docker-up docker-down install dev-local hooks

lint:
	-uv run ruff format .
	-uv run ruff check --fix .
	-uv run pyrefly check .

test:
	uv run pytest

# Default install: piighost from PyPI as recorded in uv.lock. Works
# out of the box with no flags; CI, Docker, and fresh clones all use
# this path.
install:
	uv sync

# Local-dev install. Layers an editable install of the sibling
# ../piighost on top of `uv sync`, so in-flight library changes are
# picked up without a republish. Caveat: any subsequent `uv sync` (or
# `uv run`, which auto-syncs) reinstalls piighost from PyPI and
# undoes the editable. Re-run this target after such a sync.
dev-local: install
	uv pip install -e ../piighost --reinstall-package piighost

# Install the prek-managed git hook that blocks a commit when uv.lock
# would record piighost as a local editable source. Defense in depth:
# if a developer accidentally edited the lockfile via ``uv sync`` after
# a manual editable install, the hook catches it before push. Requires
# prek on PATH (``uv tool install prek``).
hooks:
	prek install

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
