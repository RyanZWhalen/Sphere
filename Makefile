.DEFAULT_GOAL := setup

PYTHON ?= python3
SPHERE_VENV := .sphere-venv
SPHERE := $(SPHERE_VENV)/bin/sphere

.PHONY: setup run

setup:
	$(PYTHON) -m venv "$(SPHERE_VENV)"
	"$(SPHERE_VENV)/bin/python" -m pip install -e ".[serve]"
	@echo
	@echo "Sphere is installed in its isolated environment. Run:"
	@echo "  $(SPHERE) <directory> [--search-root PATH]"

run:
	@test -x "$(SPHERE)" || { echo "Sphere is not installed; run 'make setup' first." >&2; exit 1; }
	"$(SPHERE)" $(ARGS)
