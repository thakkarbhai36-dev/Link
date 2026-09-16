.PHONY: install dev test lint fmt typecheck browser clean

install:
	python -m pip install -e .

dev:
	python -m pip install -e ".[dev,browser]"

test:
	pytest

lint:
	ruff check src tests

fmt:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy

browser:
	python -m playwright install chromium

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
