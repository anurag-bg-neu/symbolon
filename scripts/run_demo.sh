#!/usr/bin/env bash
# Start the GeneMatch demo: relay :8000, wallets :8101 (partner A) and :8102 (partner B),
# lab :8200, trial verifier :8300.
# Usage (macOS/Linux, or Git Bash on Windows): COUPLE=1|2|3 [EMPTY="alex"] ./scripts/run_demo.sh
# Windows PowerShell: see scripts/run_demo.ps1. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
export PYTHONIOENCODING=utf-8
if [ -x .venv/bin/python ]; then PY=.venv/bin/python            # macOS / Linux venv
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe  # Windows venv (Git Bash)
else PY=python3; fi
case "${COUPLE:-1}" in
  1) A=alex;   B=sam ;;
  2) A=jordan; B=riley ;;
  3) A=casey;  B=morgan ;;
  *) echo "COUPLE must be 1, 2 or 3" >&2; exit 1 ;;
esac
# EMPTY="alex jordan" starts those wallets with no results (re-seeds every wallet).
EMPTY_ARGS=()
for p in ${EMPTY:-}; do EMPTY_ARGS+=(--empty "$p"); done
if [ ${#EMPTY_ARGS[@]} -gt 0 ] || [ ! -f var/wallets/alex.json ] || [ ! -f var/accounts.json ] || [ ! -f var/lab_accounts.json ]; then
  "$PY" scripts/seed_demo.py "${EMPTY_ARGS[@]}"
fi

trap 'kill $(jobs -p) 2>/dev/null' EXIT
"$PY" -m uvicorn lab.service:app --port 8200 &
"$PY" -m uvicorn relay.service:app --port 8000 &
"$PY" -m uvicorn trial.service:app --port 8300 &
GENEMATCH_PERSON=$A "$PY" -m uvicorn --factory wallet.service:create_app --port 8101 &
GENEMATCH_PERSON=$B "$PY" -m uvicorn --factory wallet.service:create_app --port 8102 &
echo "Couple ${COUPLE:-1}: $A on http://localhost:8101, $B on http://localhost:8102"
echo "Trial verifier on http://localhost:8300"
wait
