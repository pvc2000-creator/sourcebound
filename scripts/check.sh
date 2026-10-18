#!/usr/bin/env bash
# Full local quality gate: format, lint, types (Python + JS), unit/integration tests, browser tests.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

echo "== ruff format --check"; ruff format --check .
echo "== ruff check";          ruff check .
echo "== mypy";                mypy --python-executable "$(command -v "$PY")"
echo "== tsc (frontend)";      tsc -p tsconfig.json
echo "== unit + integration";  "$PY" -m unittest discover -s tests -t .
if [ "${SKIP_E2E:-0}" != "1" ]; then
  echo "== browser end-to-end"; "$PY" tests/e2e/test_ui.py
fi
echo "All checks passed."
