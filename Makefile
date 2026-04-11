# z3rno-server — developer Makefile
#
# Operational targets for local development. Wraps docker compose for the
# dev stack and delegates Python-specific targets (lint/test/format) to uv.
#
# Most common workflow:
#   make dev-up          # start postgres + valkey + server + worker
#   make dev-logs        # follow container logs
#   make dev-psql        # psql shell into postgres
#   make dev-down        # stop the stack (keeps volumes)
#   make clean           # stop + delete volumes (DESTRUCTIVE)
#
# For schema migrations, cd into z3rno-core and run `make migrate`.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE_FILE := docker-compose.dev.yml
COMPOSE := docker compose -f $(COMPOSE_FILE)

# Colours for help output — works on any terminal that supports ANSI.
BOLD := \033[1m
DIM := \033[2m
RESET := \033[0m

.DEFAULT_GOAL := help

## help                 Show this help message
.PHONY: help
help:
	@printf "$(BOLD)z3rno-server — developer Makefile$(RESET)\n\n"
	@printf "$(BOLD)Usage:$(RESET) make $(DIM)<target>$(RESET)\n\n"
	@printf "$(BOLD)Targets:$(RESET)\n"
	@awk 'BEGIN {FS = ":.*?## "} /^## / {sub(/^## /, ""); print "  " $$0}' $(MAKEFILE_LIST)

# =============================================================================
# Dev stack (docker compose)
# =============================================================================

## dev-up               Start the dev stack in the background (postgres + valkey + server + worker)
.PHONY: dev-up
dev-up:
	@$(COMPOSE) up -d
	@printf "\n$(BOLD)Dev stack is up.$(RESET) Health:\n"
	@$(COMPOSE) ps

## dev-down             Stop the dev stack (keeps named volumes)
.PHONY: dev-down
dev-down:
	@$(COMPOSE) down

## dev-restart          Restart all services (useful after config changes)
.PHONY: dev-restart
dev-restart:
	@$(COMPOSE) restart

## dev-logs             Follow logs from all services (Ctrl+C to stop)
.PHONY: dev-logs
dev-logs:
	@$(COMPOSE) logs -f --tail=200

## dev-ps               Show service status (containers + health)
.PHONY: dev-ps
dev-ps:
	@$(COMPOSE) ps

## dev-psql             Open a psql shell into the postgres container
.PHONY: dev-psql
dev-psql:
	@$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-z3rno} -d $${POSTGRES_DB:-z3rno}

## dev-valkey           Open a valkey-cli shell into the valkey container
.PHONY: dev-valkey
dev-valkey:
	@$(COMPOSE) exec valkey valkey-cli

## dev-build            Rebuild the server + worker images from Dockerfile
.PHONY: dev-build
dev-build:
	@$(COMPOSE) build --no-cache server worker

## clean                Stop the stack AND remove named volumes (DESTRUCTIVE)
.PHONY: clean
clean:
	@printf "$(BOLD)This will delete the postgres + valkey volumes.$(RESET)\n"
	@read -rp "Continue? [y/N] " ans && [ "$$ans" = "y" ] || exit 1
	@$(COMPOSE) down -v
	@printf "Volumes removed.\n"

# =============================================================================
# Python developer targets (skeleton-mode until pyproject.toml lands)
# =============================================================================

## test                 Run pytest (skeleton-mode until pyproject.toml lands)
.PHONY: test
test:
	@if [ -f pyproject.toml ]; then \
		uv run pytest -v; \
	else \
		echo "skeleton mode: no pyproject.toml yet — nothing to test"; \
	fi

## lint                 Run ruff check + mypy (skeleton-mode until pyproject.toml lands)
.PHONY: lint
lint:
	@if [ -f pyproject.toml ]; then \
		uv run ruff check . && uv run mypy .; \
	else \
		echo "skeleton mode: no pyproject.toml yet — nothing to lint"; \
	fi

## format               Run ruff format (skeleton-mode until pyproject.toml lands)
.PHONY: format
format:
	@if [ -f pyproject.toml ]; then \
		uv run ruff format .; \
	else \
		echo "skeleton mode: no pyproject.toml yet — nothing to format"; \
	fi

## install              Install Python dependencies via uv sync
.PHONY: install
install:
	@if [ -f pyproject.toml ]; then \
		uv sync --all-extras --dev; \
	else \
		echo "skeleton mode: no pyproject.toml yet — run this after Week 3 pyproject.toml lands"; \
	fi
