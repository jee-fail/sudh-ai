.PHONY: setup venv up down test

PYTHON ?= python3
VENV_DIR := .venv
VENV_READY := $(VENV_DIR)/.requirements-installed

up:
	docker compose up --build -d

down:
	docker compose down

setup: venv

venv: $(VENV_READY)

$(VENV_READY): backend/requirements.txt backend/requirements-dev.txt
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/python -m pip install -r backend/requirements-dev.txt
	touch $(VENV_READY)

test: venv
	cd backend && ../$(VENV_DIR)/bin/python -m pytest -q
