"""Lab service: issues signed carrier credentials and claims (synthetic demo lab).

Only the lab decides what a credential contains. Patients sign in to the lab to
approve a wallet connection or download their own results file; they can never
choose genes. Lab staff edit synthetic records on a staff-only page.
Run: uvicorn lab.service:app --port 8200
"""

from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from core import crypto
from core.issuance import IssuanceError, issue
from core.labfile import make_lab_file
from core.models import CLAIM_FINDING, MAX_SET
from lab import auth, keys

ROOT = Path(__file__).resolve().parents[1]
RECORDS_FILE = ROOT / "data" / "lab_records.json"
CONNECT_TTL = 10 * 60
MAX_FORM = 16 * 1024
# Hosts a wallet may ask to be sent back to after consent. Tests extend this.
ALLOWED_RETURN_HOSTS = {"localhost", "127.0.0.1"}
BANNER = "Demo with synthetic data. Not medical advice."
SAFE_NEXT = ("/my-results", "/staff/records")

app = FastAPI(title="Symbolon lab")
CONNECT: dict[str, dict[str, Any]] = {}
SESSIONS = auth.LabSessions()


class Finding(BaseModel):
    gene: str
    finding: str


class CredentialRequest(BaseModel):
    person_id: str
    genes: list[str]
    findings: list[Finding] = []
    holder_pubkey: str


class ConnectRequest(BaseModel):
    person_id: str
    holder_pubkey: str
    return_url: str
    state: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


# --- configuration -----------------------------------------------------------------


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


def show_demo_logins() -> bool:
    return os.environ.get("GENEMATCH_SHOW_DEMO_LOGINS", "1") != "0"


def public_base() -> str:
    return os.environ.get("GENEMATCH_LAB_PUBLIC", "http://localhost:8200").rstrip("/")


def overlay_path() -> Path:
    return keys.var_dir() / "lab_records.json"


def load_records() -> dict[str, dict[str, Any]]:
    """Synthetic records: data/lab_records.json plus staff edits in var/lab_records.json."""
    data = json.loads(RECORDS_FILE.read_text("utf-8"))
    records = {k: dict(v) for k, v in data.items() if not k.startswith("_")}
    path = overlay_path()
    if path.exists():
        for pid, genes in json.loads(path.read_text("utf-8")).items():
            if pid in records:
                records[pid]["genes"] = list(genes)
    return records


def accounts() -> dict[str, dict[str, Any]]:
    return auth.load_accounts(keys.var_dir() / "lab_accounts.json")


def ar_panel() -> list[dict[str, Any]]:
    return [e for e in keys.load_panel() if e.get("inheritance") == "AR"]


# --- pages ---------------------------------------------------------------------------

# The lab is a separate organization in the same calm, Apple-inspired layout as the
# wallet (white header, whitespace, rounded cards, pill buttons). Only the accent
# differs: deep violet (#4b2e83, ~10:1 on white) for buttons, links, and the mark.
LAB_CSS = """
:root{--ink:#1d1d1f;--muted:#515154;--line:#d2d2d7;--accent:#4b2e83;--soft:#f3effa;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Helvetica,Arial,sans-serif;
color:var(--ink);-webkit-font-smoothing:antialiased}
*{box-sizing:border-box}
body{margin:0;background:#fbfbfd;line-height:1.55;font-size:17px}
a{color:var(--accent)}
.banner{background:var(--ink);color:#fff;text-align:center;padding:7px 12px;font-size:.85rem;position:sticky;top:0;z-index:10}
header{max-width:760px;margin:0 auto;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;color:var(--ink);text-decoration:none;letter-spacing:-.01em}
.brand small{display:block;font-weight:500;font-size:.8rem;color:var(--accent)}
.mark{width:30px;height:30px;flex:none}.who{color:var(--muted);font-size:.9rem;margin-right:8px}
header button{padding:6px 14px;font-size:.9rem}
main{max-width:640px;margin:0 auto;padding:8px 24px 72px}
h1{font-size:clamp(1.9rem,5vw,2.6rem);line-height:1.12;letter-spacing:-.02em;margin:28px 0 14px}
h2{font-size:1.2rem;margin:0 0 10px;letter-spacing:-.01em}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;margin:20px 0}
.muted{color:var(--muted)}.small{font-size:.9rem}.tag{display:inline-block;background:var(--soft);
color:var(--accent);border-radius:999px;padding:3px 12px;font-size:.8rem;font-weight:600}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;align-items:center}
button,.btn{font:inherit;font-weight:600;border-radius:999px;padding:11px 22px;border:2px solid var(--accent);
cursor:pointer;text-decoration:none;display:inline-block;line-height:1.2}
main,h1{animation:reveal .45s ease-out both}
@keyframes reveal{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:480px){body{font-size:16px}header,main{padding-left:16px;padding-right:16px}.card{padding:18px}}
.primary{background:var(--accent);color:#fff}.secondary{background:#fff;color:var(--accent)}
button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid #f0b429;outline-offset:2px}
label{display:block;font-weight:600;margin:12px 0 4px}
input[type=email],input[type=password]{font:inherit;padding:11px 14px;border:1px solid var(--line);
border-radius:12px;width:100%;background:#fff}fieldset{border:1px solid var(--line);border-radius:14px;padding:14px}
.genes{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.genes label{font-weight:400;margin:0}.notice{background:#fff6e0;border-left:4px solid #b7891f;
border-radius:8px;padding:10px 14px}.chips{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.chips li{background:var(--soft);color:var(--accent);border-radius:999px;padding:4px 12px;font-weight:600}
form.inline{display:inline}
"""


LAB_MARK = ('<svg class="mark" viewBox="0 0 32 32" aria-hidden="true">'
            '<rect x="1" y="1" width="30" height="30" rx="9" fill="#f3effa"/>'
            '<path d="M12 6h8M14 6v8l-6 11a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-11V6" '
            'fill="none" stroke="#4b2e83" stroke-width="2.4" stroke-linejoin="round"/>'
            '<path d="M10.5 21h11" stroke="#4b2e83" stroke-width="2.4"/></svg>')


def _page(title: str, body: str, ident: dict | None = None, status: int = 200) -> HTMLResponse:
    """Server-rendered lab page. Every dynamic value is passed through html.escape."""
    who = ""
    if ident:
        label = "Lab staff" if ident["role"] == "staff" else _name(ident["person_id"])
        who = (f'<span class="who">Signed in: {html.escape(label)}</span> '
               '<form class="inline" method="post" action="/signout">'
               '<button class="secondary" type="submit">Sign out</button></form>')
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)} · Symbolon Demo Lab</title><style>{LAB_CSS}</style></head><body>"
        f"<div class=\"banner\" role=\"note\">{BANNER}</div>"
        f"<header><a class=\"brand\" href=\"/\">{LAB_MARK}"
        "<span>Symbolon Demo Lab<small>Patient portal</small></span></a>"
        f"<div>{who}</div></header>"
        f"<main>{body}</main></body></html>", status_code=status)


def _name(person_id: str | None) -> str:
    rec = load_records().get(person_id or "", {})
    return rec.get("display_name") or (person_id or "").capitalize()


def _ident(request: Request) -> dict[str, Any] | None:
    return SESSIONS.get(request.cookies.get(auth.COOKIE))


def _to_signin(next_url: str) -> RedirectResponse:
    return RedirectResponse(f"/signin?next={quote(next_url, safe='/')}", status_code=303)


def _safe_next(value: str) -> str:
    if value in SAFE_NEXT:
        return value
    parts = value.split("/")
    if len(parts) == 3 and parts[1] == "connect" and len(parts[2]) == 32 and all(
            c in "0123456789abcdef" for c in parts[2]):
        return value
    return "/my-results"


async def _form(request: Request) -> dict[str, list[str]]:
    raw = await request.body()
    if len(raw) > MAX_FORM:
        raise HTTPException(413, "form too large")
    return parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not auth.same_origin(request):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
    return await call_next(request)


# --- sign-in -------------------------------------------------------------------------


def _signin_page(next_url: str, error: str | None = None, status: int = 200) -> HTMLResponse:
    err = f'<div class="notice" role="alert"><p>{html.escape(error)}</p></div>' if error else ""
    demo = ""
    if show_demo_logins():
        rows = "".join(f"<li>{html.escape(r['display_name'])}: {html.escape(auth.patient_email(p))} / "
                       f"{html.escape(auth.patient_password(p))}</li>" for p, r in load_records().items())
        demo = (f'<div class="card"><p class="small"><strong>Demo lab accounts</strong> (synthetic data)</p>'
                f'<ul class="small">{rows}<li>Lab staff: {auth.STAFF_EMAIL} / {auth.STAFF_PASSWORD}</li></ul></div>')
    return _page("Sign in", f"""
<h1>Patient portal sign-in</h1>
<p class="muted">Sign in with your Symbolon Demo Lab account. This is separate from your Symbolon wallet.</p>
{err}
<form class="card" method="post" action="/signin">
  <input type="hidden" name="next" value="{html.escape(next_url)}">
  <label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <div class="actions"><button class="primary" type="submit">Sign in</button></div>
</form>{demo}""", status=status)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Portal home: sign-in when signed out, otherwise your results (or staff records)."""
    if _ident(request) is None:
        return _signin_page("/my-results")
    return RedirectResponse("/my-results", status_code=303)


@app.get("/signin", response_class=HTMLResponse)
def signin_page(next: str = "/my-results"):
    return _signin_page(_safe_next(next))


@app.post("/signin")
async def signin(request: Request):
    form = await _form(request)
    next_url = _safe_next((form.get("next") or [""])[0])
    rec = auth.authenticate(accounts(), (form.get("email") or [""])[0],
                            (form.get("password") or [""])[0])
    if rec is None:
        return _signin_page(next_url, "That email and password don't match. Please try again.", 401)
    resp = RedirectResponse(next_url, status_code=303)
    resp.set_cookie(auth.COOKIE, SESSIONS.create({"role": rec["role"], "person_id": rec["person_id"]}),
                    max_age=SESSIONS.ttl, path="/", httponly=True, samesite="lax")
    return resp


@app.post("/signout")
def signout(request: Request):
    SESSIONS.revoke(request.cookies.get(auth.COOKIE))
    resp = RedirectResponse("/signin", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# --- existing API --------------------------------------------------------------------


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


# --- issuing from the lab's own records -----------------------------------------------


def issue_for(person_id: str, holder_pubkey: str) -> dict[str, Any]:
    """Issue the lab's own synthetic record for this person (never caller-chosen genes)."""
    rec = load_records()[person_id]
    k_lab, seed = keys.load_or_create()
    cred, claims = issue(k_lab, seed, person_id, rec["genes"],
                         [{"gene": g, "finding": CLAIM_FINDING} for g in rec["findings"]],
                         holder_pubkey, keys.load_panel(), salt_commitment=salt_flag())
    return {"lab_file": make_lab_file(seed, cred, sorted(rec["genes"]), rec["display_name"]),
            "claims": [c.model_dump() for c in claims]}


def own_results_file(person_id: str) -> dict[str, Any]:
    """Carrier-only lab file for this person's own record."""
    rec = load_records()[person_id]
    k_lab, seed = keys.load_or_create()
    cred, _ = issue(k_lab, seed, person_id, rec["genes"], [], bytes(32), keys.load_panel(),
                    salt_commitment=salt_flag())
    return make_lab_file(seed, cred, sorted(rec["genes"]), rec["display_name"])


def make_custom_lab_file(person_id: str, genes: list[str], display_name: str) -> dict[str, Any]:
    """Lab-side tooling (scripts/make_lab_file.py, tests): issue chosen genes.

    Not reachable over HTTP. Running it requires the lab's key file, i.e. being the lab.
    """
    if person_id not in load_records():
        raise IssuanceError("unknown person")
    if not display_name.strip() or len(display_name) > 40:
        raise IssuanceError("display name must be 1 to 40 characters")
    k_lab, seed = keys.load_or_create()
    cred, _ = issue(k_lab, seed, person_id, genes, [], bytes(32), keys.load_panel(),
                    salt_commitment=salt_flag())
    return make_lab_file(seed, cred, sorted(genes), display_name.strip())


# --- Connect your lab ------------------------------------------------------------------


def _sweep() -> None:
    now = time.time()
    for rid in [r for r, v in CONNECT.items() if v["expires"] < now]:
        CONNECT.pop(rid, None)


def _valid_return(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return (u.scheme in ("http", "https") and u.hostname in ALLOWED_RETURN_HOSTS
            and u.path == "/connect/callback" and not u.query and not u.fragment
            and u.username is None and u.password is None)


def _connect(rid: str) -> dict[str, Any]:
    _sweep()
    entry = CONNECT.get(rid)
    if entry is None:
        raise HTTPException(404, "unknown or expired request")
    return entry


@app.post("/connect/requests")
def connect_request(body: ConnectRequest) -> dict:
    """Called by the wallet backend. Releases nothing until the patient signs in and approves."""
    _sweep()
    if body.person_id not in load_records():
        raise HTTPException(404, "the lab has no results for this person")
    try:
        crypto.from_hex(body.holder_pubkey, 32)
    except (TypeError, ValueError):
        raise HTTPException(422, "holder_pubkey must be 32 bytes of hex") from None
    if not _valid_return(body.return_url):
        raise HTTPException(422, "return_url must be a local wallet /connect/callback address")
    rid = secrets.token_hex(16)
    token = secrets.token_hex(32)
    CONNECT[rid] = {"person_id": body.person_id, "holder_pubkey": body.holder_pubkey,
                    "return_url": body.return_url, "state": body.state, "collect_token": token,
                    "status": "pending", "result": None,
                    "expires": time.time() + CONNECT_TTL}
    return {"request_id": rid, "consent_url": f"{public_base()}/connect/{rid}",
            "collect_token": token}


def _wrong_patient_page(ident: dict, entry: dict) -> HTMLResponse:
    who = "lab staff" if ident["role"] == "staff" else _name(ident["person_id"])
    return _page("Not your request", f"""
<h1>This request isn't for your account.</h1>
<section class="card">
  <p>You're signed in to the lab as {html.escape(who)}. This wallet belongs to {html.escape(_name(entry['person_id']))}.
  Only {html.escape(_name(entry['person_id']))} can send their results to it.</p>
  <p class="muted small">Nothing was sent.</p>
  <form method="post" action="/signout"><button class="secondary" type="submit">Sign out and switch account</button></form>
</section>""", ident, status=403)


def _check_patient(request: Request, entry: dict) -> dict[str, Any]:
    ident = _ident(request)
    if ident is None:
        raise HTTPException(401, "sign in to the lab first")
    if ident["role"] != "patient" or ident["person_id"] != entry["person_id"]:
        raise HTTPException(403, "only the patient can answer this request")
    return ident


@app.get("/connect/{rid}", response_class=HTMLResponse)
def connect_consent(rid: str, request: Request):
    entry = _connect(rid)
    ident = _ident(request)
    if ident is None:
        return _to_signin(f"/connect/{rid}")
    if ident["role"] != "patient" or ident["person_id"] != entry["person_id"]:
        return _wrong_patient_page(ident, entry)
    if entry["status"] != "pending":
        return _page("Already answered", "<h1>This request was already answered.</h1>"
                     "<p class=\"muted\">You can close this page.</p>", ident)
    rec = load_records()[entry["person_id"]]
    name = html.escape(rec["display_name"])
    n_genes, n_find = len(rec["genes"]), len(rec["findings"])
    findings = (f" and {n_find} finding{'s' if n_find != 1 else ''} you can share with a "
                "clinical trial" if n_find else "")
    rid_e = html.escape(rid)
    return _page("Send your results to your wallet", f"""
<h1>Send your carrier results to your Symbolon wallet?</h1>
<section class="card">
  <p><span class="tag">Synthetic demo data</span></p>
  <p>Results for <strong>{name}</strong>: {n_genes} carrier result{'s' if n_genes != 1 else ''}{findings}.</p>
  <p class="muted small">The lab signs your results so nobody can fake them. They go only to your
  wallet. The lab never sees your wallet's private key.</p>
  <div class="actions">
    <form method="post" action="/connect/{rid_e}/approve"><button class="primary" type="submit">Send</button></form>
    <form method="post" action="/connect/{rid_e}/decline"><button class="secondary" type="submit">Don't send</button></form>
  </div>
</section>""", ident)


def _back(entry: dict, outcome: str) -> RedirectResponse:
    q = urlencode({"state": entry["state"], "outcome": outcome})
    return RedirectResponse(f"{entry['return_url']}?{q}", status_code=303)


@app.post("/connect/{rid}/approve")
def connect_approve(rid: str, request: Request):
    entry = _connect(rid)
    _check_patient(request, entry)
    if entry["status"] != "pending":
        raise HTTPException(409, "already answered")
    entry["result"] = issue_for(entry["person_id"], entry["holder_pubkey"])
    entry["status"] = "approved"
    return _back(entry, "approved")


@app.post("/connect/{rid}/decline")
def connect_decline(rid: str, request: Request):
    entry = _connect(rid)
    _check_patient(request, entry)
    if entry["status"] != "pending":
        raise HTTPException(409, "already answered")
    entry["status"] = "declined"
    return _back(entry, "declined")


@app.get("/connect/{rid}/result")
def connect_result(rid: str, request: Request):
    entry = _connect(rid)
    token = request.headers.get("x-collect-token", "")
    if not hmac.compare_digest(token.encode(), entry["collect_token"].encode()):
        raise HTTPException(404, "unknown or expired request")
    if entry["status"] == "pending":
        return JSONResponse({"status": "pending"}, status_code=202)
    if entry["status"] == "declined":
        CONNECT.pop(rid, None)
        return JSONResponse({"status": "declined"}, status_code=409)
    CONNECT.pop(rid, None)  # single use
    return {"status": "approved", **entry["result"]}


# --- patients: your own results --------------------------------------------------------


@app.get("/my-results", response_class=HTMLResponse)
def my_results(request: Request):
    ident = _ident(request)
    if ident is None:
        return _to_signin("/my-results")
    if ident["role"] == "staff":
        return RedirectResponse("/staff/records", status_code=303)
    rec = load_records()[ident["person_id"]]
    chips = "".join(f"<li>{html.escape(g)}</li>" for g in sorted(rec["genes"])) or "<li>none</li>"
    finds = ", ".join(html.escape(f) for f in rec["findings"]) or "none"
    return _page("Your results", f"""
<h1>Your results, {html.escape(rec['display_name'])}.</h1>
<section class="card">
  <p><span class="tag">Synthetic demo data</span></p>
  <h2>Carrier results</h2><ul class="chips">{chips}</ul>
  <p class="small muted">Trial findings: {finds}. These go to your wallet only through "Connect your lab".</p>
  <div class="actions"><a class="btn primary" href="/my-results/file">Download your results file</a></div>
  <p class="small muted">Add this file to your Symbolon wallet with "Add your lab file". It is signed by the lab,
  so it can't be changed. Your results are set by the lab; you can't edit them here.</p>
</section>""", ident)


@app.get("/my-results/file")
def my_results_file(request: Request):
    ident = _ident(request)
    if ident is None:
        return _to_signin("/my-results")
    if ident["role"] != "patient":
        raise HTTPException(403, "only patients can download their results")
    doc = own_results_file(ident["person_id"])
    return Response(json.dumps(doc, indent=2), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{ident["person_id"]}-lab-results.json"'})


# --- lab staff: patient records ----------------------------------------------------------


def _require_staff(request: Request) -> dict[str, Any] | RedirectResponse:
    ident = _ident(request)
    if ident is None:
        return _to_signin("/staff/records")
    if ident["role"] != "staff":
        raise HTTPException(403, "lab staff only")
    return ident


def _records_page(ident: dict, message: str | None = None, error: str | None = None,
                  status: int = 200) -> HTMLResponse:
    note = ""
    if message:
        note = f'<div class="card" role="status"><p>{html.escape(message)}</p></div>'
    if error:
        note = f'<div class="notice" role="alert"><p>{html.escape(error)}</p></div>'
    panel = ar_panel()
    blocks = []
    for pid, rec in load_records().items():
        boxes = "".join(
            f'<label><input type="checkbox" name="genes" value="{html.escape(e["gene"])}"'
            f'{" checked" if e["gene"] in rec["genes"] else ""}> {html.escape(e["gene"])} '
            f'<span class="muted small">{html.escape(e["condition"])}</span></label>' for e in panel)
        blocks.append(f"""
<form class="card" method="post" action="/staff/records/{html.escape(pid)}">
  <h2>{html.escape(rec['display_name'])}</h2>
  <fieldset><legend class="small">Carrier results (autosomal recessive only, up to {MAX_SET})</legend>
  <div class="genes">{boxes}</div></fieldset>
  <div class="actions"><button class="primary" type="submit">Save {html.escape(rec['display_name'])}'s record</button></div>
</form>""")
    return _page("Patient records", f"""
<h1>Patient records</h1>
<p class="muted">Lab staff only. Synthetic patients. Saved results reach a patient the next time they connect
their wallet or download their results file. Reseeding the demo resets these records.</p>
{note}{''.join(blocks)}""", ident, status=status)


@app.get("/staff/records", response_class=HTMLResponse)
def staff_records(request: Request):
    ident = _require_staff(request)
    if isinstance(ident, RedirectResponse):
        return ident
    return _records_page(ident)


@app.post("/staff/records/{person_id}", response_class=HTMLResponse)
async def staff_save(person_id: str, request: Request):
    ident = _require_staff(request)
    if isinstance(ident, RedirectResponse):
        raise HTTPException(401, "sign in to the lab first")
    records = load_records()
    if person_id not in records:
        raise HTTPException(404, "unknown patient")
    genes = (await _form(request)).get("genes", [])
    try:
        # Same checks as issuance: panel, AR, duplicates, MAX_SET.
        k_lab, seed = keys.load_or_create()
        issue(k_lab, seed, person_id, genes, [], bytes(32), keys.load_panel())
    except IssuanceError as e:
        return _records_page(ident, error=f"Not saved: {e}.", status=400)
    path = overlay_path()
    overlay = json.loads(path.read_text("utf-8")) if path.exists() else {}
    overlay[person_id] = sorted(genes)
    keys.write_private(path, overlay)
    return _records_page(ident, message=f"Saved {records[person_id]['display_name']}'s record.")
