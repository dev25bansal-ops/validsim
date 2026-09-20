# =============================================================================
# ValidSim — developer entry points (local parity with CI)
# Run `make help` to see targets. Requires GNU Make; on Windows use WSL/Git-Bash
# or run the equivalent commands shown in each recipe.
# =============================================================================

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip
IMAGE  ?= validsim:local

.PHONY: help install test cov lint run metrics health cli-run worker jobs smoke docker-build docker-up docker-down clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install runtime deps (requirements.txt) + dev/test tooling (requirements-dev.txt)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

test: ## Run the full pytest suite (same command as CI)
	$(PYTHON) -m pytest tests/ -v

cov: ## Run pytest with coverage and enforce the 90% floor (same as CI)
	$(PYTHON) -m pytest tests/ --cov=validsim --cov-report=term-missing --cov-fail-under=90

lint: ## Lint the package and tests with ruff (same command as CI)
	ruff check validsim tests

run: ## Serve the FastAPI app locally on :8000 with autoreload
	uvicorn validsim.api.main:app --host 0.0.0.0 --port 8000 --reload

metrics: ## Scrape the Prometheus /metrics endpoint from a running server (requires `make run` in another shell)
	$(PYTHON) -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode())"

health: ## Print a local readiness summary via the CLI (mirrors /api/v1/health; no server needed)
	$(PYTHON) -m validsim.cli health

cli-run: ## Run a quick 100-episode validation sweep via the Typer CLI
	$(PYTHON) -m validsim.cli run --episodes 100

worker: ## Run the job worker against the configured queue (Ctrl-C to stop)
	$(PYTHON) -m validsim.cli worker

jobs: ## List queued validation jobs from the configured job queue
	$(PYTHON) -m validsim.cli jobs

smoke: ## End-to-end CLI smoke: small validation -> gate -> report (chained)
	$(PYTHON) -m validsim.cli run --episodes 20 --threshold 50 && $(PYTHON) -m validsim.cli gate --latest && $(PYTHON) -m validsim.cli report --latest

docker-build: ## Build the production image locally
	docker build -t $(IMAGE) .

docker-up: ## Bring up the full local stack (api + redis + postgres)
	docker compose up --build -d

docker-down: ## Stop the local stack (add ARGS=-v to also drop volumes)
	docker compose down $(ARGS)

clean: ## Remove Python caches and build artifacts
	find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".ruff_cache" -o -name ".mypy_cache" \) -prune -exec rm -rf {} +
	rm -rf *.egg-info build dist .coverage htmlcov
