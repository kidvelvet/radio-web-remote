#!/usr/bin/env sh
# Set up a development environment without admin rights.
#
# Creates .tools/ (a venv holding only uv) with the system Python 3.11+,
# then lets uv create .venv/ with the project and its dev dependencies.
# Nothing is installed outside this folder.
set -eu

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Python 3.11 or newer is required (found: $("$PY" --version 2>&1))." >&2
    echo "Set PYTHON=/path/to/python3.11+ and run again." >&2
    exit 1
fi

if [ ! -x .tools/bin/uv ]; then
    echo "Creating .tools/ with uv"
    "$PY" -m venv .tools
    .tools/bin/python -m pip install --quiet --upgrade pip
    .tools/bin/python -m pip install --quiet uv
fi

.tools/bin/uv sync --python "$PY"

echo
echo "Ready. Run the tests with:"
echo "  .tools/bin/uv run pytest"
