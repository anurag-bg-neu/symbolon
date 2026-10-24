"""Wallet backend: holds one person's credential and runs the PSI through the relay.

The session scalar lives only inside core.protocol.Session in this process and is
zeroed when a check ends. Results are computed here, never at the relay.
Run: GENEMATCH_PERSON=alex uvicorn --factory wallet.service:create_app --port 8101
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.models import Mode, ProtocolError, Result
from core.protocol import Session
from lab import keys

log = logging.getLogger("genematch.wallet")
STATIC = Path(__file__).resolve().parent / "static"
INVALID_INVITE = {"code": "INVALID_INVITE", "user_message":
                  "That invite code isn't valid or was already used. Nothing was shared."}
MODE_TEXT = {
    "count": "You will both learn only how many carrier genes you share, not which ones.",
    "detail": "You will both learn which carrier genes you share. "
              "Neither of you learns the genes only the other carries.",
}
# Relay states that mean the other side stopped the check.
STOPPED = {"declined": "PEER_DECLINED", "failed": "SESSION_EXPIRED",
           "expired": "SESSION_EXPIRED"}


class StartBody(BaseModel):
    mode: Mode


class JoinBody(BaseModel):
    invite_code: str


def _new_check(**kw: Any) -> dict[str, Any]:
    return {"state": "idle", "mode": None, "round": None, "result": None,
            "explanation": None, "error": None, "sid": None, "role": None,
            "session": None, "task": None, "counselor_input": None, **kw}


def create_app(person_id: str | None = None,
               relay: httpx.AsyncClient | None = None) -> FastAPI:
    person_id = person_id or os.environ.get("GENEMATCH_PERSON", "alex")
    wallet = json.loads((keys.var_dir() / "wallets" / f"{person_id}.json").read_text())
    relay = relay or httpx.AsyncClient(
        base_url=os.environ.get("GENEMATCH_RELAY", "http://localhost:8000"), timeout=5)
    poll = float(os.environ.get("GENEMATCH_POLL", "0.5"))
    panel = {e["gene"]: e for e in keys.load_panel()}
    app = FastAPI(title=f"GeneMatch wallet ({person_id})")
    holder = {"check": _new_check()}
    app.state.holder = holder  # lets tests inspect the live Session

    @app.exception_handler(ProtocolError)
    async def protocol_error(_req, e: ProtocolError):
        return JSONResponse({"error": e.to_dict()}, status_code=400)

    # --- relay calls -------------------------------------------------------

    async def call(method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await relay.request(method, path, **kw)
        except httpx.HTTPError:
            raise ProtocolError("RELAY_UNAVAILABLE") from None
        if r.status_code >= 500:
            raise ProtocolError("RELAY_UNAVAILABLE")
        if r.status_code == 404 and "/sessions/" in path and not path.endswith("/join"):
            raise ProtocolError("SESSION_EXPIRED")
        return r

    async def relay_state(sid: str) -> str:
        state = (await call("GET", f"/sessions/{sid}/state")).json()["state"]
        if state in STOPPED:
            raise ProtocolError(STOPPED[state])
        return state

    async def post_round(sid: str, n: int, role: str, payload: dict) -> None:
        r = await call("POST", f"/sessions/{sid}/round/{n}",
                       json={"role": role, "payload": payload})
        if r.status_code != 200:
            await relay_state(sid)  # raises if the peer stopped
            raise ProtocolError("OUT_OF_ORDER", "relay refused round")

    async def wait_peer(sid: str, n: int, role: str) -> dict:
        while True:
            r = await call("GET", f"/sessions/{sid}/round/{n}", params={"role": role})
            if r.status_code == 200:
                return r.json()
            await relay_state(sid)
            await asyncio.sleep(poll)

    # --- the check itself --------------------------------------------------

    async def run(check: dict[str, Any]) -> None:
        sid, role, mode = check["sid"], check["role"], check["mode"]
        s = None
        try:
            if role == "initiator":
                while await relay_state(sid) != "accepted":
                    await asyncio.sleep(poll)
                check["state"] = "running"
            # Checks our own credential; raises BAD_CREDENTIAL before anything is sent.
            s = check["session"] = Session(role, mode, sid, wallet["credential"],
                                           wallet["genes"], wallet["lab_pubkey"])
            check["round"] = 0
            await post_round(sid, 0, role, s.round0().model_dump())
            s.receive_commitment(await wait_peer(sid, 0, role))
            check["round"] = 1
            await post_round(sid, 1, role, s.round1().model_dump())
            peer1 = await wait_peer(sid, 1, role)
            check["round"] = 2
            await post_round(sid, 2, role, s.round2(peer1).model_dump())
            result = s.result(await wait_peer(sid, 2, role))
        except ProtocolError as e:
            log.info("check failed: %s", e.code)
            check.update(state="failed", error=e.to_dict())
            if e.code not in ("PEER_DECLINED", "SESSION_EXPIRED", "RELAY_UNAVAILABLE"):
                try:
                    await relay.post(f"/sessions/{sid}/fail", json={"role": role})
                except httpx.HTTPError:
                    pass
            return
        except Exception:
            log.exception("unexpected wallet error")
            check.update(state="failed", error={
                "code": "INTERNAL", "user_message":
                "Something went wrong, so the check was stopped. Nothing about you was revealed."})
            return
        finally:
            if s is not None:
                s.zero()
        check.update(state="done", result=result.model_dump())
        await counsel(check, result)

    async def counsel(check: dict[str, Any], result: Result) -> None:
        """Call P3's counselor if it exists. It sees only the agreed result."""
        try:
            from agent.counselor import explain
        except ImportError:
            return
        if result.mode == "count":
            check["counselor_input"] = {"shared_count": result.shared_count}
        else:
            check["counselor_input"] = {"shared": [panel[g] for g in result.shared_genes]}
        try:
            out = await asyncio.to_thread(explain, result, list(panel.values()))
            check["explanation"] = out.model_dump() if hasattr(out, "model_dump") else out
        except Exception:
            log.warning("counselor failed; no explanation")

    def reset(**kw: Any) -> dict[str, Any]:
        old = holder["check"]
        if old["task"] is not None:
            old["task"].cancel()
        if old["session"] is not None:
            old["session"].zero()
        holder["check"] = _new_check(**kw)
        return holder["check"]

    def require(state: str) -> dict[str, Any]:
        check = holder["check"]
        if check["state"] != state:
            raise ProtocolError("OUT_OF_ORDER", f"wallet is {check['state']}")
        return check

    # --- endpoints -----------------------------------------------------------

    @app.get("/me")
    def me() -> dict:
        return {"person_id": wallet["person_id"], "genes": wallet["genes"],
                "count": len(wallet["genes"]),
                "claims": [{"gene": c["gene"], "finding": c["finding"]}
                           for c in wallet["claims"]]}

    @app.post("/check/start")
    async def start(body: StartBody) -> dict:
        r = await call("POST", "/sessions", json={"mode": body.mode})
        data = r.json()
        check = reset(state="waiting_for_partner", mode=body.mode,
                      sid=data["session_id"], role="initiator")
        check["task"] = asyncio.create_task(run(check))
        return {"session_id": data["session_id"], "invite_code": data["invite_code"]}

    @app.post("/check/join")
    async def join(body: JoinBody):
        r = await call("POST", "/sessions/join", json={"invite_code": body.invite_code})
        if r.status_code != 200:
            return JSONResponse({"error": INVALID_INVITE}, status_code=400)
        data = r.json()
        # Joining sends no protocol data; nothing leaves this wallet until accept.
        reset(state="awaiting_accept", mode=data["mode"], sid=data["session_id"],
              role="joiner")
        return {"session_id": data["session_id"], "mode": data["mode"],
                "mode_text": MODE_TEXT[data["mode"]]}

    @app.post("/check/accept")
    async def accept() -> dict:
        check = require("awaiting_accept")
        r = await call("POST", f"/sessions/{check['sid']}/accept")
        if r.status_code != 200:
            raise ProtocolError("OUT_OF_ORDER", "relay refused accept")
        check["state"] = "running"
        check["task"] = asyncio.create_task(run(check))
        return {"ok": True}

    @app.post("/check/decline")
    async def decline() -> dict:
        check = require("awaiting_accept")
        await call("POST", f"/sessions/{check['sid']}/decline")
        check["state"] = "declined"
        return {"ok": True}

    @app.get("/check/status")
    def status() -> dict:
        c = holder["check"]
        return {k: c[k] for k in ("state", "mode", "round", "result", "explanation", "error")}

    @app.get("/check/seen")
    async def seen() -> dict:
        check = holder["check"]
        t = check["session"].transcript() if check["session"] else {"sent": {}, "received": {}}
        relay_log: list[dict] = []
        if check["sid"]:
            try:
                r = await relay.get(f"/sessions/{check['sid']}/log")
                relay_log = r.json() if r.status_code == 200 else []
            except httpx.HTTPError:
                pass
        me_items = _items(t["received"], "from your partner")
        if check["result"]:
            me_items.append({"label": "result", "value": check["result"]})
        return {"parties": [
            {"party": "me", "items": me_items},
            {"party": "partner", "items": _items(t["sent"], "from you")},
            {"party": "relay", "items": [
                {"label": f"round {e['round']} {e['kind']} from {e['role']}",
                 "value": e["prefixes"]} for e in relay_log]},
            {"party": "agent", "items": [{"label": "counselor input",
                                          "value": check["counselor_input"]}]
             if check["counselor_input"] is not None else []},
        ]}

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def _items(rounds: dict[str, Any], source: str) -> list[dict]:
    """Transcript rounds as {label, value}: 8-hex prefixes and counts only."""
    names = {"round0": "commitment", "round1": "blinded set", "round2": "double-blinded set"}
    items = []
    for key in ("round0", "round1", "round2"):
        if key not in rounds:
            continue
        v = rounds[key]
        if isinstance(v, str):
            items.append({"label": f"{names[key]} {source}", "value": v})
        else:
            items.append({"label": f"{names[key]} {source}: count", "value": v["count"]})
            items.append({"label": f"{names[key]} {source}: points", "value": v["points"]})
    return items
