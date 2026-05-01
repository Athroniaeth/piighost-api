.PHONY: lint test docker-up docker-down install install-pypi hooks

lint:
	-uv run ruff format .
	-uv run ruff check --fix .
	-uv run pyrefly check .

test:
	uv run pytest

# Default dev install. Pyproject's [tool.uv.sources] points piighost at
# ../piighost (editable), so source changes there are picked up live.
install:
	uv sync

# Same as install but ignores pyproject sources, so piighost comes from
# PyPI instead of the local checkout. Use this before committing the
# lockfile, or to reproduce the production install on the host.
install-pypi:
	uv sync --no-sources

# Install the prek-managed git hook that blocks a commit when uv.lock
# records piighost as a local editable source. Requires prek on PATH
# (`uv tool install prek`).
hooks:
	prek install

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
