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
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import crypto
from core.claims import make_presentation, select_claim
from core.labfile import LabFileError, verify_connect_claims, verify_lab_file
from core.models import Mode, ProtocolError, Result, SignedClaim
from core.protocol import Session
from lab import keys
from wallet.auth import Accounts, Sessions, demo_email, demo_password, load_accounts

log = logging.getLogger("genematch.wallet")
STATIC = Path(__file__).resolve().parent / "static"
PAGES = Path(__file__).resolve().parent / "pages"
MAX_UPLOAD = 100 * 1024
PENDING_TTL = 10 * 60
# Paths that need a signed-in session. Everything else (landing, sign-in, static
# assets, /auth/*) is public and holds no personal data.
PROTECTED_API = ("/me", "/check/", "/present", "/onboard/")
SIGNED_OUT = {"code": "SIGNED_OUT", "user_message": "Please sign in to use your wallet."}
NO_RESULTS = {"code": "NO_RESULTS", "user_message":
              "Add your lab results first. Nothing was shared."}
BUSY = {"code": "CHECK_IN_PROGRESS", "user_message":
        "Finish or leave the current check before changing your results. Nothing was changed."}
LAB_UNAVAILABLE = {"code": "LAB_UNAVAILABLE", "user_message":
                   "We couldn't reach the lab, so nothing was changed. Please try again."}
RAW_DNA = {"code": "RAW_DNA", "user_message":
           "This looks like a raw DNA file. GeneMatch only accepts results signed by a lab, "
           "so nobody can fake them. Ask your lab for a GeneMatch results file. "
           "Nothing was stored."}
FILE_TOO_LARGE = {"code": "FILE_TOO_LARGE", "user_message":
                  "This file is too large to be a GeneMatch lab file (limit 100 KB). "
                  "Nothing was stored."}
NOT_JSON = {"code": "NOT_LAB_FILE", "user_message":
            "This isn't a GeneMatch lab file. Nothing was stored."}
EXPIRED_UPLOAD = {"code": "UPLOAD_EXPIRED", "user_message":
                  "That upload expired. Please add the file again. Nothing was changed."}


def looks_like_raw_dna(data: bytes, filename: str = "") -> bool:
    """FASTQ, VCF, BAM/gzip, or consumer genotype exports (rsid tables)."""
    name = filename.lower()
    if name.endswith((".fastq", ".fq", ".vcf", ".bam", ".cram", ".gz", ".fastq.gz",
                      ".vcf.gz", ".bcf")):
        return True
    if data[:2] == b"\x1f\x8b" or data[:4] == b"BAM\x01":
        return True
    head = data[:4096].decode("utf-8", "replace")
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if head.startswith("##fileformat=VCF") or any(ln.startswith("#CHROM") for ln in lines[:50]):
        return True
    if len(lines) >= 4 and lines[0].startswith("@") and lines[2].startswith("+"):
        return True  # FASTQ record
    if re.search(r"^#?\s*rsid\b", head, re.IGNORECASE | re.MULTILINE):
        return True
    if sum(1 for ln in lines[:50] if re.match(r"^(rs|i)\d+[\t,]", ln)) >= 3:
        return True  # 23andMe / AncestryDNA style rows
    return False
INVALID_INVITE = {"code": "INVALID_INVITE", "user_message":
                  "That invite code isn't valid or was already used. Nothing was shared."}
# Both modes also reveal each person's total carrier count (the lab-signed count is
# sent in round 1), so consent text must say so (Req 3.2).
COUNT_DISCLOSURE = (" You will also each see how many carrier genes the other person "
                    "has in total, but not which ones.")
MODE_TEXT = {
    "count": "You will both learn only how many carrier genes you share, not which ones."
             + COUNT_DISCLOSURE,
    "detail": "You will both learn which carrier genes you share. "
              "Neither of you learns the genes only the other carries." + COUNT_DISCLOSURE,
}
# Relay states that mean the other side stopped the check.
STOPPED = {"declined": "PEER_DECLINED", "failed": "PEER_FAILED",
           "expired": "SESSION_EXPIRED"}
FORBIDDEN_ORIGIN = {"code": "FORBIDDEN_ORIGIN", "user_message":
                    "This request came from another website, so it was refused. "
                    "Nothing was shared."}
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
TRIAL_UNAVAILABLE = {"code": "TRIAL_UNAVAILABLE", "user_message":
                     "We couldn't reach the trial site, so nothing was shared. "
                     "Please try again."}
UNKNOWN_REQUEST = {"code": "UNKNOWN_REQUEST", "user_message":
                   "This trial request doesn't exist or has expired. Nothing was shared."}


class PresentBody(BaseModel):
    request_id: str
    approve: bool


def _same_origin(request: Request) -> bool:
    """CSRF guard for state-changing requests.

    Browsers attach Origin to cross-site POSTs, including "simple" ones (form posts,
    text/plain fetches) that skip CORS preflight. Allow a POST only if it carries no
    Origin (curl, tests, server-side callers) or an Origin equal to this wallet's own.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")
    host = request.headers.get("host", "")
    return origin in (f"http://{host}", f"https://{host}")


class StartBody(BaseModel):
    mode: Mode


class JoinBody(BaseModel):
    invite_code: str


def _new_check(**kw: Any) -> dict[str, Any]:
    return {"state": "idle", "mode": None, "round": None, "result": None,
            "explanation": None, "error": None, "sid": None, "role": None,
            "session": None, "task": None, "counselor_input": None,
            "own_meta": None, "peer_meta": None, **kw}


def _signed_meta(round1: dict[str, Any]) -> dict[str, Any]:
    """Round-1 signed metadata as the transparency panel shows it.

    person_id, count, and issued_at as sent; commitment and lab_sig as 8-hex prefixes.
    """
    return {"person_id": round1["person_id"], "count": round1["count"],
            "issued_at": round1["issued_at"], "commitment": round1["commitment"][:8],
            "lab_sig": round1["lab_sig"][:8]}


class SignInBody(BaseModel):
    email: str
    password: str


class ConnectFinishBody(BaseModel):
    request_id: str


class ConfirmBody(BaseModel):
    pending_id: str


def create_app(person_id: str | None = None,
               relay: httpx.AsyncClient | None = None,
               trial: httpx.AsyncClient | None = None,
               lab: httpx.AsyncClient | None = None) -> FastAPI:
    person_id = person_id or os.environ.get("GENEMATCH_PERSON", "alex")
    wallet_path = keys.var_dir() / "wallets" / f"{person_id}.json"
    wallet = json.loads(wallet_path.read_text())
    relay = relay or httpx.AsyncClient(
        base_url=os.environ.get("GENEMATCH_RELAY", "http://localhost:8000"), timeout=5)
    trial = trial or httpx.AsyncClient(
        base_url=os.environ.get("GENEMATCH_TRIAL", "http://localhost:8300"), timeout=5)
    lab = lab or httpx.AsyncClient(
        base_url=os.environ.get("GENEMATCH_LAB", "http://localhost:8200"), timeout=5)
    presentations: list[dict[str, Any]] = []  # what this wallet sent to trials
    poll = float(os.environ.get("GENEMATCH_POLL", "0.5"))
    panel = {e["gene"]: e for e in keys.load_panel()}
    app = FastAPI(title=f"GeneMatch wallet ({person_id})")
    holder = {"check": _new_check()}
    app.state.holder = holder  # lets tests inspect the live Session

    accounts = Accounts(person_id, load_accounts(keys.var_dir() / "accounts.json"))
    sessions = Sessions()
    cookie = f"gm_session_{person_id}"  # per wallet: localhost cookies ignore the port
    app.state.sessions = sessions
    app.state.cookie_name = cookie
    connects: dict[str, dict[str, Any]] = {}   # request_id -> collect token, holder seed
    pending_uploads: dict[str, dict[str, Any]] = {}

    def signed_in(request: Request) -> bool:
        return sessions.valid(request.cookies.get(cookie))

    @app.exception_handler(ProtocolError)
    async def protocol_error(_req, e: ProtocolError):
        return JSONResponse({"error": e.to_dict()}, status_code=400)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            return JSONResponse({"error": FORBIDDEN_ORIGIN}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        if path.startswith(PROTECTED_API) and not signed_in(request):
            return JSONResponse({"error": SIGNED_OUT}, status_code=401)
        return await call_next(request)

    # --- sign-in -------------------------------------------------------------

    def _start_session(next_url: str = "/app") -> JSONResponse:
        resp = JSONResponse({"ok": True, "next": next_url})
        resp.set_cookie(cookie, sessions.create(), max_age=sessions.ttl, path="/",
                        httponly=True, samesite="lax")
        return resp

    @app.post("/auth/signin")
    def auth_signin(body: SignInBody):
        outcome, other = accounts.check(body.email, body.password)
        if outcome == "wrong_wallet":
            return JSONResponse({"error": {"code": "WRONG_WALLET", "user_message":
                                           accounts.wrong_wallet_message(other)}}, status_code=403)
        if outcome != "ok":
            return JSONResponse({"error": {"code": "BAD_LOGIN", "user_message":
                                           "That email and password don't match. Please try again."}},
                                status_code=401)
        return _start_session()

    @app.post("/auth/demo")
    def auth_demo():
        """"Continue as <name>": demo shortcut for this wallet's own account."""
        if person_id not in accounts.accounts:
            return JSONResponse({"error": {"code": "NO_ACCOUNT", "user_message":
                                           "This wallet has no demo account yet. "
                                           "Run the seed script first."}}, status_code=404)
        return _start_session()

    @app.post("/auth/signout")
    def auth_signout(request: Request):
        sessions.revoke(request.cookies.get(cookie))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(cookie, path="/")
        return resp

    @app.get("/auth/session")
    def auth_session(request: Request) -> dict:
        return {"signed_in": signed_in(request), "person_id": person_id,
                "display_name": accounts.display_name(),
                "demo_email": demo_email(person_id), "demo_password": demo_password(person_id)}

    @app.get("/signin")
    def signin_page():
        return FileResponse(STATIC / "signin.html")

    @app.get("/app")
    def app_page(request: Request):
        if not signed_in(request):
            return RedirectResponse("/signin", status_code=303)
        return FileResponse(PAGES / "app.html")

    def has_results() -> bool:
        return wallet.get("credential") is not None

    def save_wallet() -> None:
        keys.write_private(wallet_path, wallet)

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

    async def tell_relay_failed(sid: str, role: str) -> None:
        try:
            await relay.post(f"/sessions/{sid}/fail", json={"role": role})
        except httpx.HTTPError:
            pass

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
            own1 = s.round1().model_dump()
            await post_round(sid, 1, role, own1)
            check["own_meta"] = _signed_meta(own1)
            peer1 = await wait_peer(sid, 1, role)
            check["round"] = 2
            double = s.round2(peer1)  # verifies peer1 first
            check["peer_meta"] = _signed_meta(peer1)
            await post_round(sid, 2, role, double.model_dump())
            result = s.result(await wait_peer(sid, 2, role))
        except ProtocolError as e:
            log.info("check failed: %s", e.code)
            check.update(state="failed", error=e.to_dict())
            # The relay already knows about these, or cannot be reached.
            if e.code not in ("PEER_DECLINED", "PEER_FAILED", "SESSION_EXPIRED",
                              "RELAY_UNAVAILABLE"):
                await tell_relay_failed(sid, role)
            return
        except Exception as e:
            # Type name only: messages and tracebacks can carry protocol data.
            log.error("unexpected wallet error: %s", type(e).__name__)
            check.update(state="failed", error={
                "code": "INTERNAL", "user_message":
                "Something went wrong, so the check was stopped. Nothing about you was revealed."})
            await tell_relay_failed(sid, role)  # so the partner stops waiting
            return
        finally:
            if s is not None:
                s.zero()
        # Explain first, then flip to done, so a client that sees "done" also sees
        # the explanation (or null if there is no counselor or it failed).
        check["result"] = result.model_dump()
        await counsel(check, result)
        check["state"] = "done"

    async def counsel(check: dict[str, Any], result: Result) -> None:
        """Call P3's counselor if it exists. It sees only the agreed result."""
        try:
            from agent.counselor import explain
        except ImportError:
            return
        try:
            if result.mode == "count":
                check["counselor_input"] = {"shared_count": result.shared_count}
            else:
                check["counselor_input"] = {
                    "shared": [panel[g] for g in result.shared_genes]}
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
                           for c in wallet["claims"]],
                "display_name": accounts.display_name(),
                "synthetic": True, "has_results": has_results(),
                "lab_portal": os.environ.get("GENEMATCH_LAB_PUBLIC",
                                             "http://localhost:8200").rstrip("/")}

    @app.post("/check/start")
    async def start(body: StartBody):
        if not has_results():
            return JSONResponse({"error": NO_RESULTS}, status_code=409)
        r = await call("POST", "/sessions", json={"mode": body.mode})
        if r.status_code != 200:
            # call() already maps 5xx (including "relay full"); any other non-200
            # means there is no session to use, so report it instead of a KeyError.
            raise ProtocolError("RELAY_UNAVAILABLE", f"create returned {r.status_code}")
        data = r.json()
        check = reset(state="waiting_for_partner", mode=body.mode,
                      sid=data["session_id"], role="initiator")
        check["task"] = asyncio.create_task(run(check))
        return {"session_id": data["session_id"], "invite_code": data["invite_code"]}

    @app.post("/check/join")
    async def join(body: JoinBody):
        if not has_results():
            return JSONResponse({"error": NO_RESULTS}, status_code=409)
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
        me_items += _meta_items(check["peer_meta"], "from your partner")
        if check["result"]:
            me_items.append({"label": "result", "value": check["result"]})
        partner_items = _items(t["sent"], "from you") + _meta_items(check["own_meta"],
                                                                    "from you")
        relay_items: list[dict] = []
        for e in relay_log:
            relay_items.append({"label": f"round {e['round']} {e['kind']} from {e['role']}",
                                "value": e["prefixes"]})
            relay_items += _meta_items(e.get("meta"), f"from {e['role']}")
        return {"parties": [
            {"party": "me", "items": me_items},
            {"party": "partner", "items": partner_items},
            {"party": "relay", "items": relay_items},
            {"party": "agent", "items": [{"label": "counselor input",
                                          "value": check["counselor_input"]}]
             if check["counselor_input"] is not None else []},
            {"party": "trial", "items": [item for p in presentations for item in (
                {"label": f"claims shared with {p['trial_id']}", "value": 1},
                {"label": f"finding shared with {p['trial_id']}",
                 "value": f"{p['gene']}: {p['finding']}"},
                {"label": f"challenge from {p['trial_id']}", "value": p["nonce"][:8]},
            )]},
        ]}

    # --- trial eligibility (selective disclosure) ----------------------------

    def claims() -> list[SignedClaim]:
        return [SignedClaim.model_validate(c) for c in wallet["claims"]]

    async def fetch_request(request_id: str) -> dict[str, Any] | JSONResponse:
        """The trial's request, fetched server-side. Never trust nonces from the page."""
        if not REQUEST_ID.match(request_id):
            return JSONResponse({"error": UNKNOWN_REQUEST}, status_code=400)
        try:
            r = await trial.get(f"/requests/{request_id}")
            trials = (await trial.get("/trials")).json() if r.status_code == 200 else []
        except httpx.HTTPError:
            return JSONResponse({"error": TRIAL_UNAVAILABLE}, status_code=502)
        if r.status_code == 404:
            return JSONResponse({"error": UNKNOWN_REQUEST}, status_code=404)
        if r.status_code != 200:
            return JSONResponse({"error": TRIAL_UNAVAILABLE}, status_code=502)
        data = r.json()
        names = {t["trial_id"]: t["name"] for t in trials}
        data["trial_name"] = names.get(data["request"]["trial_id"], data["request"]["trial_id"])
        return data

    @app.get("/present/pending")
    async def present_pending(request_id: str):
        data = await fetch_request(request_id)
        if isinstance(data, JSONResponse):
            return data
        req = data["request"]
        try:
            select_claim(claims(), req["gene"], req["finding"])
            has = True
        except ProtocolError:
            has = False
        return {"request_id": request_id, "trial_name": data["trial_name"],
                "gene": req["gene"], "finding": req["finding"], "status": data["status"],
                "has_matching_claim": has}

    @app.post("/present")
    async def present(body: PresentBody):
        if not body.approve:
            return {"status": "declined", "code": None,
                    "reason": "You declined the request. Nothing was shared."}
        data = await fetch_request(body.request_id)
        if isinstance(data, JSONResponse):
            return data
        req = data["request"]
        if data["status"] != "pending":
            return {"status": data["status"], "code": data["code"], "reason": data["reason"]}
        try:
            claim = select_claim(claims(), req["gene"], req["finding"])
        except ProtocolError as e:
            return {"status": "no_matching_claim", "code": e.code, "reason": e.user_message}
        p = make_presentation(claim, bytes.fromhex(wallet["holder_seed"]),
                              req["nonce"], req["trial_id"])
        try:
            r = await trial.post(f"/requests/{body.request_id}/present",
                                 json={"presentation": p.model_dump()})
        except httpx.HTTPError:
            return JSONResponse({"error": TRIAL_UNAVAILABLE}, status_code=502)
        presentations.append({"trial_id": req["trial_id"], "gene": claim.gene,
                              "finding": claim.finding, "nonce": req["nonce"]})
        if r.status_code != 200:
            return JSONResponse({"error": TRIAL_UNAVAILABLE}, status_code=502)
        return r.json()

    # --- onboarding: Connect your lab -----------------------------------------

    def idle() -> bool:
        return holder["check"]["state"] in ("idle", "done", "failed", "declined")

    def apply_results(cred, genes: list[str], claims_list=None, holder_seed: str | None = None):
        wallet["credential"] = cred.model_dump()
        wallet["genes"] = genes
        if claims_list is not None:
            wallet["claims"] = [c.model_dump() for c in claims_list]
        if holder_seed is not None:
            wallet["holder_seed"] = holder_seed
        save_wallet()

    @app.post("/onboard/connect")
    async def onboard_connect(request: Request):
        if not idle():
            return JSONResponse({"error": BUSY}, status_code=409)
        seed = secrets.token_bytes(32)  # new holder key, generated here; lab sees only the public key
        pub = crypto.pubkey_from_seed(seed).hex()
        return_url = f"{request.url.scheme}://{request.headers.get('host', '')}/app"
        try:
            r = await lab.post("/connect/requests", json={
                "person_id": person_id, "holder_pubkey": pub, "return_url": return_url})
        except httpx.HTTPError:
            return JSONResponse({"error": LAB_UNAVAILABLE}, status_code=502)
        if r.status_code != 200:
            return JSONResponse({"error": LAB_UNAVAILABLE}, status_code=502)
        data = r.json()
        for rid in [k for k, v in connects.items() if v["expires"] < time.time()]:
            connects.pop(rid, None)
        connects[data["request_id"]] = {"token": data["collect_token"], "seed": seed.hex(),
                                        "pub": pub, "expires": time.time() + PENDING_TTL}
        return {"consent_url": data["consent_url"]}

    @app.post("/onboard/connect/finish")
    async def onboard_connect_finish(body: ConnectFinishBody):
        pend = connects.get(body.request_id)
        if pend is None or pend["expires"] < time.time():
            return JSONResponse({"error": {"code": "CONNECT_EXPIRED", "user_message":
                "That lab request expired or was already used. Please connect again. "
                "Nothing was changed."}}, status_code=404)
        if not idle():
            return JSONResponse({"error": BUSY}, status_code=409)
        try:
            r = await lab.get(f"/connect/{body.request_id}/result",
                              headers={"X-Collect-Token": pend["token"]})
        except httpx.HTTPError:
            return JSONResponse({"error": LAB_UNAVAILABLE}, status_code=502)
        if r.status_code == 202:
            return {"status": "pending"}
        connects.pop(body.request_id, None)
        if r.status_code == 409:
            return {"status": "declined", "reason": "You chose not to send results. "
                                                    "Nothing was changed."}
        if r.status_code != 200:
            return JSONResponse({"error": LAB_UNAVAILABLE}, status_code=502)
        data = r.json()
        try:
            cred, genes, name = verify_lab_file(data.get("lab_file"), wallet["lab_pubkey"],
                                                person_id)
            claim_objs = verify_connect_claims(data.get("claims"), wallet["lab_pubkey"],
                                               person_id, pend["pub"])
        except LabFileError as e:
            return JSONResponse({"error": e.to_dict()}, status_code=400)
        apply_results(cred, genes, claim_objs, pend["seed"])
        return {"status": "connected", "count": len(genes), "findings": len(claim_objs)}

    # --- onboarding: Add your lab file -----------------------------------------

    @app.post("/onboard/upload")
    async def onboard_upload(request: Request):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_UPLOAD):
            return JSONResponse({"error": FILE_TOO_LARGE}, status_code=413)
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD:
                return JSONResponse({"error": FILE_TOO_LARGE}, status_code=413)
        filename = request.headers.get("x-file-name", "")
        if looks_like_raw_dna(data, filename):
            return JSONResponse({"error": RAW_DNA}, status_code=400)
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse({"error": NOT_JSON}, status_code=400)
        try:
            cred, genes, name = verify_lab_file(doc, wallet["lab_pubkey"], person_id)
        except LabFileError as e:
            return JSONResponse({"error": e.to_dict()}, status_code=400)
        for pid in [k for k, v in pending_uploads.items() if v["expires"] < time.time()]:
            pending_uploads.pop(pid, None)
        pid = secrets.token_hex(16)
        pending_uploads[pid] = {"cred": cred, "genes": genes, "expires": time.time() + PENDING_TTL}
        return {"pending_id": pid, "display_name": name, "genes": genes, "count": len(genes),
                "replaces": has_results(), "current_count": len(wallet["genes"])}

    @app.post("/onboard/upload/confirm")
    def onboard_upload_confirm(body: ConfirmBody):
        pend = pending_uploads.pop(body.pending_id, None)
        if pend is None or pend["expires"] < time.time():
            return JSONResponse({"error": EXPIRED_UPLOAD}, status_code=404)
        if not idle():
            return JSONResponse({"error": BUSY}, status_code=409)
        apply_results(pend["cred"], pend["genes"])  # claims and holder key are kept
        return {"status": "replaced", "count": len(pend["genes"])}

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


META_LABELS = {"person_id": "person ID", "count": "carrier count", "issued_at": "issue time",
               "commitment": "credential commitment", "lab_sig": "lab signature"}


def _meta_items(meta: dict[str, Any] | None, source: str) -> list[dict]:
    """Round-1 signed metadata as {label, value}: plain IDs, counts, times, 8-hex prefixes.

    Values from the relay log are untrusted; the UI must render them as text.
    """
    if not isinstance(meta, dict):
        return []
    return [{"label": f"{label} {source}", "value": meta[key]}
            for key, label in META_LABELS.items() if key in meta]


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
