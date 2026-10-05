.PHONY: help install sync lock lint format test test-unit test-integration test-js clean

PROJECT := velib

help:
	@echo "Available targets:"
	@echo "  install           Install all dependency groups via uv"
	@echo "  sync              Alias for install"
	@echo "  lock              Regenerate uv.lock"
	@echo "  lint              Run pre-commit on all files"
	@echo "  format            Apply ruff format + autofix"
	@echo "  test              Full test suite with coverage"
	@echo "  test-unit         Unit tests only"
	@echo "  test-integration  Integration tests only"
	@echo "  test-js           Map JavaScript tests (node --test)"
	@echo "  clean             Remove caches and build artifacts"

install:
	uv sync --locked --all-groups

sync: install

lock:
	uv lock

lint:
	uv run pre-commit run --all-files

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
	node --test "tests/js/*.test.mjs"

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-js:
	node --test "tests/js/*.test.mjs"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
