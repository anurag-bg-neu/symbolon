"""Untrusted relay: pairs two wallets and forwards opaque round payloads.

In-memory only. Enforces commit-then-reveal through the release rules in
design.md. Never logs payloads; the transparency log keeps 8-hex prefixes only.
Run: uvicorn relay.service:app --port 8000
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Path, Response
from pydantic import BaseModel

from core.models import Mode, Role

TTL = 24 * 3600
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KINDS = {0: "commitment", 1: "blinded_set", 2: "double_blinded"}
PEER = {"initiator": "joiner", "joiner": "initiator"}

SESSIONS: dict[str, dict[str, Any]] = {}
CODES: dict[str, str] = {}  # invite code -> session_id

app = FastAPI(title="GeneMatch relay")


class CreateBody(BaseModel):
    mode: Mode


class JoinBody(BaseModel):
    invite_code: str


class RoleBody(BaseModel):
    role: Role


class RoundBody(BaseModel):
    role: Role
    payload: dict[str, Any]


def _sweep() -> None:
    cutoff = time.time() - TTL
    for sid in [s for s, v in SESSIONS.items() if v["created"] < cutoff]:
        CODES.pop(SESSIONS.pop(sid)["code"], None)


def _get(sid: str) -> dict[str, Any]:
    _sweep()
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown or expired session")
    return SESSIONS[sid]


def _move(sess: dict[str, Any], allowed: tuple[str, ...], new: str) -> dict:
    if sess["state"] not in allowed:
        raise HTTPException(409, f"not allowed in state {sess['state']}")
    sess["state"] = new
    return {"ok": True}


def _prefixes(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("h", "blinded", "points"):
        value = payload.get(key)
        values = value if isinstance(value, list) else [value]
        out += [v[:8] for v in values if isinstance(v, str)]
    return out


@app.post("/sessions")
def create(body: CreateBody) -> dict:
    _sweep()
    sid = secrets.token_hex(16)
    code = "".join(secrets.choice(CROCKFORD) for _ in range(8))
    while code in CODES:
        code = "".join(secrets.choice(CROCKFORD) for _ in range(8))
    SESSIONS[sid] = {"mode": body.mode, "state": "waiting", "created": time.time(),
                     "code": code, "rounds": {}, "log": []}
    CODES[code] = sid
    return {"session_id": sid, "invite_code": code}


@app.post("/sessions/join")
def join(body: JoinBody) -> dict:
    _sweep()
    sid = CODES.get(body.invite_code.strip().upper())
    if sid is None:
        raise HTTPException(404, "unknown invite code")
    sess = SESSIONS[sid]
    _move(sess, ("waiting",), "joined")
    return {"session_id": sid, "mode": sess["mode"]}


@app.post("/sessions/{sid}/accept")
def accept(sid: str) -> dict:
    return _move(_get(sid), ("joined",), "accepted")


@app.post("/sessions/{sid}/decline")
def decline(sid: str) -> dict:
    return _move(_get(sid), ("joined",), "declined")


@app.post("/sessions/{sid}/fail")
def fail(sid: str, body: RoleBody) -> dict:
    return _move(_get(sid), ("waiting", "joined", "accepted"), "failed")


@app.get("/sessions/{sid}/state")
def state(sid: str) -> dict:
    sess = _get(sid)
    return {"state": sess["state"], "mode": sess["mode"]}


@app.post("/sessions/{sid}/round/{n}")
def post_round(body: RoundBody, sid: str, n: int = Path(ge=0, le=2)) -> dict:
    sess = _get(sid)
    rounds = sess["rounds"]
    if sess["state"] != "accepted":
        raise HTTPException(409, "rounds are only accepted after the joiner accepts")
    if (n, body.role) in rounds:
        raise HTTPException(409, "round already posted")
    if n > 0 and (n - 1, body.role) not in rounds:
        raise HTTPException(409, "previous round missing")
    rounds[(n, body.role)] = body.payload
    sess["log"].append({
        "t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "role": body.role, "round": n, "kind": KINDS[n],
        "prefixes": _prefixes(body.payload),
    })
    if (2, "initiator") in rounds and (2, "joiner") in rounds:
        sess["state"] = "done"
    return {"ok": True}


@app.get("/sessions/{sid}/round/{n}")
def get_round(sid: str, role: Role, n: int = Path(ge=0, le=2)):
    rounds = _get(sid)["rounds"]
    peer = PEER[role]
    both_committed = (0, "initiator") in rounds and (0, "joiner") in rounds
    releasable = (n, peer) in rounds and (n == 2 or both_committed)
    if not releasable:
        return Response(status_code=204)
    return rounds[(n, peer)]


@app.get("/sessions/{sid}/log")
def log(sid: str) -> list[dict]:
    return list(_get(sid)["log"])
