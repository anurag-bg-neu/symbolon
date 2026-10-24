"""Trial verifier: requests one lab-signed finding and checks it.

The trial never sees tokens, other claims, or carrier results. Each request carries
a fresh nonce that is single-use and expires after 5 minutes. In-memory store.
Run: uvicorn trial.service:app --port 8300
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from core.claims import verify_presentation
from core.issuance import format_time
from core.models import CLAIM_FINDING, CLAIM_GENES, Presentation, ProtocolError, TrialRequest

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
TRIALS_FILE = ROOT / "data" / "trials.json"
TTL = 5 * 60
ELIGIBLE = "Eligible — verified by lab"
PENDING = "Waiting for the patient to approve the request."

# Used only if data/trials.json (P3) is missing. Synthetic demo trials.
DEMO_TRIALS = [
    {"trial_id": "parp-demo-01", "name": "PARP inhibitor study (demo)",
     "summary": "Synthetic demo trial for people with a germline BRCA1 pathogenic variant.",
     "required": {"gene": "BRCA1", "finding": CLAIM_FINDING}},
    {"trial_id": "parp-demo-02", "name": "BRCA2 maintenance study (demo)",
     "summary": "Synthetic demo trial for people with a germline BRCA2 pathogenic variant.",
     "required": {"gene": "BRCA2", "finding": CLAIM_FINDING}},
]


def load_trials() -> list[dict[str, Any]]:
    trials = json.loads(TRIALS_FILE.read_text("utf-8")) if TRIALS_FILE.exists() else DEMO_TRIALS
    # Germline BRCA findings only; anything else could never be issued by the lab.
    return [t for t in trials if t.get("required", {}).get("gene") in CLAIM_GENES]


class NewRequest(BaseModel):
    trial_id: str


class PresentBody(BaseModel):
    presentation: dict[str, Any]


def _fetch_lab_pubkey() -> str:
    if os.environ.get("GENEMATCH_LAB_PUBKEY"):
        return os.environ["GENEMATCH_LAB_PUBKEY"]
    base = os.environ.get("GENEMATCH_LAB", "http://localhost:8200")
    return httpx.get(f"{base}/pubkey", timeout=5).json()["ed25519_pubkey"]


def create_app(lab_pubkey: str | None = None,
               clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(title="GeneMatch trial verifier")
    requests: dict[str, dict[str, Any]] = {}
    lab_key: dict[str, str | None] = {"value": lab_pubkey}
    app.state.requests = requests  # lets tests inspect the store

    def get_lab_key() -> str:
        if lab_key["value"] is None:
            lab_key["value"] = _fetch_lab_pubkey()
        return lab_key["value"]

    def trial_by_id(trial_id: str) -> dict[str, Any]:
        for t in load_trials():
            if t["trial_id"] == trial_id:
                return t
        raise HTTPException(404, "unknown trial")

    def lookup(request_id: str) -> dict[str, Any]:
        entry = requests.get(request_id)
        if entry is None:
            raise HTTPException(404, "unknown request")
        if entry["status"] == "pending" and clock() > entry["expires"]:
            _reject(entry, ProtocolError("NONCE_EXPIRED"))
        return entry

    def _reject(entry: dict[str, Any], e: ProtocolError) -> None:
        entry.update(status="rejected", code=e.code, reason=e.user_message)

    def view(entry: dict[str, Any]) -> dict[str, Any]:
        return {k: entry[k] for k in ("request", "status", "code", "reason", "received")}

    @app.get("/trials")
    def trials() -> list[dict[str, Any]]:
        return load_trials()

    @app.post("/requests")
    def new_request(body: NewRequest) -> dict[str, Any]:
        t = trial_by_id(body.trial_id)
        now = clock()
        req = TrialRequest(
            request_id=secrets.token_hex(16), trial_id=t["trial_id"],
            nonce=secrets.token_hex(16), gene=t["required"]["gene"],
            finding=t["required"]["finding"],
            expires_at=format_time(datetime.fromtimestamp(now + TTL, timezone.utc)))
        requests[req.request_id] = {
            "request": req.model_dump(), "expires": now + TTL, "used": False,
            "status": "pending", "code": None, "reason": PENDING,
            "received": {"claims": 0, "gene": None, "finding": None}}
        return req.model_dump()

    @app.get("/requests/{request_id}")
    def get_request(request_id: str) -> dict[str, Any]:
        return view(lookup(request_id))

    @app.post("/requests/{request_id}/present")
    def present(request_id: str, body: PresentBody) -> dict[str, Any]:
        entry = lookup(request_id)
        try:
            if entry["used"]:
                raise ProtocolError("NONCE_REUSED")
            if entry["status"] == "rejected" and entry["code"] == "NONCE_EXPIRED":
                raise ProtocolError("NONCE_EXPIRED")
            entry["used"] = True  # one presentation per request, valid or not
            try:
                p = Presentation.model_validate(body.presentation)
            except ValidationError:
                raise ProtocolError("BAD_LAB_SIG", "malformed presentation") from None
            entry["received"] = {"claims": 1, "gene": p.claim.gene,
                                 "finding": p.claim.finding}
            verify_presentation(p, get_lab_key(), TrialRequest(**entry["request"]))
        except ProtocolError as e:
            if e.code != "NONCE_REUSED":  # a replay must not overwrite the first outcome
                _reject(entry, e)
            return {"status": "rejected", "code": e.code, "reason": e.user_message}
        entry.update(status="eligible", code=None, reason=ELIGIBLE)
        return {"status": "eligible", "code": None, "reason": ELIGIBLE}

    if (STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    else:
        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return ("<!doctype html><title>GeneMatch trial verifier</title>"
                    "<p><strong>Demo with synthetic data. Not medical advice.</strong></p>"
                    "<p>Verifier page not built yet. API: <code>GET /trials</code>, "
                    "<code>POST /requests</code>.</p>")
    return app


app = create_app()
