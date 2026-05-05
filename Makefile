.PHONY: dev test lint clean

dev:
	docker compose up --build

test:
	uv run pytest tests/ -v --cov=registry --cov-report=term-missing

lint:
	uv run ruff check src/
	uv run mypy src/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .coverage htmlcov dist build
