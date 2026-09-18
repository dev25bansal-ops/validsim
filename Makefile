# =============================================================================
# ValidSim — developer entry points (local parity with CI)
# Run `make help` to see targets. Requires GNU Make; on Windows use WSL/Git-Bash
# or run the equivalent commands shown in each recipe.
# =============================================================================

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip
IMAGE  ?= validsim:local

.PHONY: help install test lint run cli-run docker-build docker-up docker-down clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install runtime deps + lint tooling from requirements.txt
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install "ruff>=0.5"

test: ## Run the full pytest suite (same command as CI)
	$(PYTHON) -m pytest tests/ -v

lint: ## Lint the package and tests with ruff (same command as CI)
	ruff check validsim tests

run: ## Serve the FastAPI app locally on :8000 with autoreload
	uvicorn validsim.api.main:app --host 0.0.0.0 --port 8000 --reload

cli-run: ## Run a quick 100-episode validation sweep via the Typer CLI
	$(PYTHON) -m validsim.cli run --episodes 100

docker-build: ## Build the production image locally
	docker build -t $(IMAGE) .

docker-up: ## Bring up the full local stack (api + redis + postgres)
	docker compose up --build -d

docker-down: ## Stop the local stack (add ARGS=-v to also drop volumes)
	docker compose down $(ARGS)

clean: ## Remove Python caches and build artifacts
	find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".ruff_cache" -o -name ".mypy_cache" \) -prune -exec rm -rf {} +
	rm -rf *.egg-info build dist .coverage htmlcov
