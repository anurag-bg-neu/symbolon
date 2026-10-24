"""Lab service: issues signed carrier credentials and claims.

Run: uvicorn lab.service:app --port 8200
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from core import crypto
from core.issuance import IssuanceError, issue
from lab import keys

app = FastAPI(title="GeneMatch lab")


class Finding(BaseModel):
    gene: str
    finding: str


class CredentialRequest(BaseModel):
    person_id: str
    genes: list[str]
    findings: list[Finding] = []
    holder_pubkey: str


def salt_flag() -> bool:
    return os.environ.get("GENEMATCH_COMMIT_SALT", "1") != "0"


def issuing_open() -> bool:
    """POST /credentials is off unless GENEMATCH_LAB_OPEN=1.

    The endpoint has no authentication, and lab tokens are only meaningful if the
    lab issues real results. An open endpoint lets anyone mint tokens for any genes
    (up to MAX_SET) and probe a partner for them. The seed script issues in-process
    and does not need it. Never enable it on a deployed service.
    """
    return os.environ.get("GENEMATCH_LAB_OPEN", "0") == "1"


@app.get("/pubkey")
def pubkey() -> dict:
    _, seed = keys.load_or_create()
    return {"ed25519_pubkey": crypto.pubkey_from_seed(seed).hex()}


@app.post("/credentials")
def credentials(req: CredentialRequest) -> dict:
    if not issuing_open():
        raise HTTPException(403, "issuance over HTTP is disabled (set GENEMATCH_LAB_OPEN=1)")
    k_lab, seed = keys.load_or_create()
    try:
        cred, claims = issue(k_lab, seed, req.person_id, req.genes,
                             [f.model_dump() for f in req.findings], req.holder_pubkey,
                             keys.load_panel(), salt_commitment=salt_flag())
    except (IssuanceError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return {"credential": cred.model_dump(), "claims": [c.model_dump() for c in claims]}
