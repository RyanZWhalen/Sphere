#!/usr/bin/env bash
# demo.sh — builds a self-contained Python topology for Sphere to visualize.
#
# Creates one sample project and two environments so the graph always has
# something interesting to show, on any machine:
#   demo/.venv-good    : satisfies the project's requirements   (all edges green)
#   demo/.venv-broken  : six at the wrong version, idna missing (mismatch + missing)
# The sample project itself has NO venv, so its folder resolves to the global
# interpreter — the "I just have a folder and ran python and it broke" case.
#
# Run:
#   ./demo.sh
#   python3 -m sphere.introspect --indent 2 --search-root demo demo/sample-project
#
# Override the base interpreter with:  PYTHON=/path/to/python3 ./demo.sh

set -euo pipefail

# --- locate a base interpreter ---------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: python3 not found on PATH (set PYTHON=/path/to/python3 to override)" >&2
  exit 1
fi
echo "Base interpreter: $("$PY" -c 'import sys; print(sys.executable, ".".join(map(str, sys.version_info[:3])))')"

# --- clean any previous run ------------------------------------------------
DEMO_DIR="demo"
PROJECT_DIR="$DEMO_DIR/sample-project"
rm -rf "$DEMO_DIR/.venv-good" "$DEMO_DIR/.venv-broken"
mkdir -p "$PROJECT_DIR"

# --- the sample project's declared requirements ----------------------------
# Tiny, pure-Python, universal wheels: fast to install, no build step, no GPU.
cat > "$PROJECT_DIR/requirements.txt" <<'EOF'
six==1.16.0
idna>=3.0
EOF

cat > "$PROJECT_DIR/app.py" <<'EOF'
import six
import idna
print("sample project running on six", six.__version__)
EOF

# helper: create a venv and install packages into it, quietly
make_venv () {
  local path="$1"; shift
  "$PY" -m venv "$path"
  "$path/bin/python" -m pip install --quiet --upgrade pip >/dev/null
  if [ "$#" -gt 0 ]; then
    "$path/bin/python" -m pip install --quiet "$@" >/dev/null
  fi
}

echo "Creating demo/.venv-good   (satisfies requirements)..."
make_venv "$DEMO_DIR/.venv-good" "six==1.16.0" "idna>=3.0"

echo "Creating demo/.venv-broken (six wrong version, idna missing)..."
make_venv "$DEMO_DIR/.venv-broken" "six==1.15.0"

echo
echo "Done. Three-state topology is ready. Point Sphere at it:"
echo
echo "    python3 -m sphere.introspect --indent 2 --search-root $DEMO_DIR $PROJECT_DIR"
echo
