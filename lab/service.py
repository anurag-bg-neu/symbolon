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


@app.get("/pubkey")
def pubkey() -> dict:
    _, seed = keys.load_or_create()
    return {"ed25519_pubkey": crypto.pubkey_from_seed(seed).hex()}


@app.post("/credentials")
def credentials(req: CredentialRequest) -> dict:
    k_lab, seed = keys.load_or_create()
    try:
        cred, claims = issue(k_lab, seed, req.person_id, req.genes,
                             [f.model_dump() for f in req.findings], req.holder_pubkey,
                             keys.load_panel(), salt_commitment=salt_flag())
    except (IssuanceError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return {"credential": cred.model_dump(), "claims": [c.model_dump() for c in claims]}
