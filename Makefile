.DEFAULT_GOAL := setup

PYTHON ?= python3
SPHERE_VENV := .sphere-venv
SPHERE := $(SPHERE_VENV)/bin/sphere

.PHONY: setup demo run

setup:
	$(PYTHON) -m venv "$(SPHERE_VENV)"
	"$(SPHERE_VENV)/bin/python" -m pip install -e ".[serve]"
	PYTHON="$(PYTHON)" ./demo.sh
	@echo
	@echo "Sphere and the three-state demo are ready. Run:"
	@echo "  $(SPHERE) demo/sample-project --search-root demo"

demo:
	PYTHON="$(PYTHON)" ./demo.sh

run:
	@test -x "$(SPHERE)" || { echo "Sphere is not installed; run 'make setup' first." >&2; exit 1; }
	"$(SPHERE)" $(ARGS)
