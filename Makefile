VENV := .venv
PY := $(VENV)/bin/python
PYTHON ?= python3

.PHONY: setup test lint fmt

$(PY):
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || { \
	  echo "error: aihealth needs Python 3.11 or newer, but '$(PYTHON)' is $$($(PYTHON) -V 2>&1 || echo 'not found')." >&2; \
	  echo "  macOS ships 3.9. Install a newer one (brew install python, or uv python install 3.12)," >&2; \
	  echo "  then run: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	$(PYTHON) -m venv $(VENV)

setup: $(PY)
	@$(PY) -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { \
	  echo "error: $(VENV) uses $$($(PY) -V 2>&1), but aihealth needs 3.11 or newer." >&2; \
	  echo "  Remove it (rm -rf $(VENV)) and run: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e '.[dev]'

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff format .
