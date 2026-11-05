.PHONY: help install test lint fmt check docker smoke clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'
	@echo ""
	@echo "Activate first: . .venv/bin/activate"

install: ## Create the venv and install with dev extras
	uv venv --python 3.12 .venv
	. .venv/bin/activate && uv pip install -e ".[dev]"

test: ## Run the test suite
	. .venv/bin/activate && pytest -q

lint: ## Ruff check and format check
	. .venv/bin/activate && ruff check src tests scripts
	. .venv/bin/activate && ruff format --check src tests scripts

fmt: ## Format and autofix
	. .venv/bin/activate && ruff format src tests scripts
	. .venv/bin/activate && ruff check --fix src tests scripts

check: lint test ## Everything CI runs locally

docker: ## Build the optional container image
	docker build -t manapool-mcp:latest .

smoke: ## Drive the server over real MCP stdio (hits the network)
	. .venv/bin/activate && python scripts/smoke.py

clean: ## Remove build and test artefacts
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
