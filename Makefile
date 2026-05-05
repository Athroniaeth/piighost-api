.PHONY: lint test docker-up docker-down install dev-local hooks

lint:
	-uv run ruff format .
	-uv run ruff check --fix .
	-uv run pyrefly check .

test:
	uv run pytest

# Default install: piighost from PyPI as recorded in uv.lock. Works
# out of the box with no flags; CI, Docker, and fresh clones all use
# this path. Mirrors the production install.
install:
	uv sync

# Local-dev install. Pulls every optional extra (langfuse, opik,
# dataset) so the full toolchain works (CLI dataset commands, all
# observation backends), then layers an editable install of the
# sibling ../piighost on top so in-flight library changes are picked
# up without a republish. ``--python .venv`` is essential: ``uv pip
# install`` otherwise honours an ambient VIRTUAL_ENV and may target the
# wrong venv (e.g. the lib's own .venv left over from a previous shell
# session). Caveat: any subsequent ``uv sync`` (or ``uv run``, which
# auto-syncs) reinstalls piighost from PyPI and undoes the editable.
# Re-run this target after such a sync, or set ``UV_NO_SYNC=1`` in
# the shell to silence the auto-sync.
dev-local:
	uv sync --all-extras
	uv pip install --python .venv -e ../piighost --reinstall-package piighost

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
