.PHONY: lint test docker-up docker-down

lint:
	-uv run ruff format .
	-uv run ruff check --fix .
	-uv run pyrefly check .

test:
	uv run pytest

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
