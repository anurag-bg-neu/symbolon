#!/usr/bin/env bash
# Start the GeneMatch demo: lab :8200, relay :8000, wallets :8101 (partner A) and :8102 (partner B).
# Usage: COUPLE=1|2|3 ./scripts/run_demo.sh   (default 1). Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
case "${COUPLE:-1}" in
  1) A=alex;   B=sam ;;
  2) A=jordan; B=riley ;;
  3) A=casey;  B=morgan ;;
  *) echo "COUPLE must be 1, 2 or 3" >&2; exit 1 ;;
esac
[ -f var/wallets/alex.json ] || "$PY" scripts/seed_demo.py

trap 'kill $(jobs -p) 2>/dev/null' EXIT
"$PY" -m uvicorn lab.service:app --port 8200 &
"$PY" -m uvicorn relay.service:app --port 8000 &
GENEMATCH_PERSON=$A "$PY" -m uvicorn --factory wallet.service:create_app --port 8101 &
GENEMATCH_PERSON=$B "$PY" -m uvicorn --factory wallet.service:create_app --port 8102 &
# Trial eligibility (deferred): "$PY" -m uvicorn trial.service:app --port 8300 &
echo "Couple ${COUPLE:-1}: $A on http://localhost:8101, $B on http://localhost:8102"
wait
