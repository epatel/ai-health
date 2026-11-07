VENV := .venv
PY := $(VENV)/bin/python

.PHONY: setup test lint fmt

$(PY):
	python3 -m venv $(VENV)

setup: $(PY)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e '.[dev]'

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff format .
