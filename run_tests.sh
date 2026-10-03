#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
CREW_PYTHON="${PYTHON_BIN:-python3}"
if [ -z "${PYTHON_BIN:-}" ] && [ -x .venv/bin/python ]; then CREW_PYTHON=.venv/bin/python; fi
"$CREW_PYTHON" scripts/preflight.py --code-only
"$CREW_PYTHON" -m compileall -q main.py ripcars_crew tests scripts
if "$CREW_PYTHON" -c 'import pyflakes' >/dev/null 2>&1; then
    "$CREW_PYTHON" -m pyflakes main.py ripcars_crew tests scripts
fi
"$CREW_PYTHON" -m unittest discover -s tests -t . -p 'test_*.py' -v
