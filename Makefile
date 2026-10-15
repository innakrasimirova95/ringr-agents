COMPOSE = docker compose
MANAGE = $(COMPOSE) exec -T api python manage.py

.PHONY: up seed test lint dbshell makemigrations down

up:
	docker build --quiet --tag ringr-agents-api .
	$(COMPOSE) up -d --no-build --wait
	$(MANAGE) migrate

seed:
	$(MANAGE) seed

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy api agent_runtime tests

dbshell:
	$(COMPOSE) exec db psql -U postgres ringr

makemigrations:
	$(MANAGE) makemigrations

down:
	$(COMPOSE) down
