VENV := .venv
PY := $(VENV)/bin/python
PYTHON ?= python3
PREFIX ?= $(HOME)/.local

.PHONY: help setup test lint fmt link unlink

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

$(PY):
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || { \
	  echo "error: aihealth needs Python 3.11 or newer, but '$(PYTHON)' is $$($(PYTHON) -V 2>&1 || echo 'not found')." >&2; \
	  echo "  macOS ships 3.9. Install a newer one (brew install python, or uv python install 3.12)," >&2; \
	  echo "  then run: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	$(PYTHON) -m venv $(VENV)

setup: $(PY)  ## Create .venv and install aihealth with dev tools (pytest, ruff)
	@$(PY) -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { \
	  echo "error: $(VENV) uses $$($(PY) -V 2>&1), but aihealth needs 3.11 or newer." >&2; \
	  echo "  Remove it (rm -rf $(VENV)) and run: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e '.[dev]'

test:  ## Run the tests
	$(VENV)/bin/pytest -q

lint:  ## Check with ruff
	$(VENV)/bin/ruff check .

fmt:  ## Format with ruff
	$(VENV)/bin/ruff format .

link: setup  ## Put `aihealth` on PATH via $(PREFIX)/bin
	mkdir -p $(PREFIX)/bin
	ln -sf $(CURDIR)/$(VENV)/bin/aihealth $(PREFIX)/bin/aihealth

unlink:  ## Remove the `aihealth` link from $(PREFIX)/bin
	rm -f $(PREFIX)/bin/aihealth
