.PHONY: test lint format security all

test:
	pytest

lint:
	ruff check .
	bandit -r db_migrator web

format:
	ruff format .

security:
	bandit -r db_migrator web -ll

all: lint test security
