.PHONY: install dev-install test lint lint-fix clean demo

install:
	pip install -e .

dev-install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

lint-fix:
	ruff check --fix .

demo:
	python scripts/demo.py

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name *.egg-info -exec rm -rf {} + 2>/dev/null || true
