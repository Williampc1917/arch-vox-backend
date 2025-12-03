.PHONY: help install run test test-unit test-integration lint format build clean setup env-check \
	run-vip-worker run-token-worker run-oauth-worker

help:
	@echo "Voice Gmail Assistant - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make setup      - Initial project setup"
	@echo "  make env-check  - Verify environment configuration"
	@echo ""
	@echo "Development:"
	@echo "  make install    - Install dependencies" 
	@echo "  make run        - Run development server"
	@echo "  make format     - Format code"
	@echo "  make lint       - Run linting"
	@echo ""
	@echo "Testing:"
	@echo "  make test       - Run all tests"
	@echo "  make test-unit  - Run unit tests only"
	@echo ""

env-check:
	@if [ ! -f .env.local ]; then \
		echo "Missing .env.local file"; \
		echo "Copy .env.example to .env.local"; \
		exit 1; \
	fi

setup:
	pip install -e ".[dev]"
	@if [ ! -f .env.local ]; then \
		cp .env.example .env.local; \
		echo "Created .env.local - please edit with your API keys"; \
	fi

install:
	pip install -e ".[dev]"

run: env-check
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-vip-worker: env-check
	SERVICE_ROLE=worker WORKER_JOB=vip_backfill python -m app.jobs.worker

run-token-worker: env-check
	SERVICE_ROLE=worker WORKER_JOB=token_refresh TOKEN_REFRESH_ENABLED=true python -m app.jobs.worker

run-oauth-worker: env-check
	SERVICE_ROLE=worker WORKER_JOB=oauth_cleanup python -m app.jobs.worker

test-unit:
	pytest tests/unit/ -v

test:
	pytest tests/ -v

format:
	black app/ tests/
	ruff check app/ tests/ --fix

lint:
	ruff check app/ tests/
	black --check app/ tests/

build:
	docker build -t voice-gmail-assistant .

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} +

security:
	bandit -r app/ tests/
