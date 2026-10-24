"""P2 backend tests: relay rules, lab service, seeding, and wallet-to-wallet PSI.

Everything runs in-process over httpx.ASGITransport; no ports are opened.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import stat
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from core import crypto
from core.models import CLAIM_FINDING
from core.protocol import Session
from lab import keys
from relay import service as relay
from tests.helpers import Lab

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)

from wallet.service import create_app  # noqa: E402

PANEL_GENES = {e["gene"] for e in keys.load_panel()}
EXPECTED = {1: ["CFTR"], 2: [], 3: ["HBB", "SMN1"]}
HEX8 = re.compile(r"^[0-9a-f]{8}$")


@pytest.fixture
def var(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setenv("GENEMATCH_POLL", "0.005")
    relay.SESSIONS.clear()
    relay.CODES.clear()
    seed_demo.seed(tmp_path)
    return tmp_path


# --- in-process wiring ------------------------------------------------------


def relay_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                             base_url="http://relay")


def wallet_client(person_id: str, rc: httpx.AsyncClient) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(person_id, rc)),
                             base_url=f"http://{person_id}")


async def wait_terminal(w: httpx.AsyncClient, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        st = (await w.get("/check/status")).json()
        if st["state"] in ("done", "failed", "declined"):
            return st
        assert asyncio.get_running_loop().time() < deadline, f"stuck in {st}"
        await asyncio.sleep(0.01)


async def run_check(a: str, b: str, mode: str, rc: httpx.AsyncClient | None = None):
    """Full check: a starts, b joins and accepts. Returns (status_a, status_b, wa, wb, sid)."""
    rc = rc or relay_client()
    wa, wb = wallet_client(a, rc), wallet_client(b, rc)
    started = (await wa.post("/check/start", json={"mode": mode})).json()
    joined = await wb.post("/check/join", json={"invite_code": started["invite_code"]})
    assert joined.status_code == 200, joined.text
    assert (await wb.post("/check/accept")).status_code == 200
    sa, sb = await asyncio.gather(wait_terminal(wa), wait_terminal(wb))
    return sa, sb, wa, wb, started["session_id"]


def relay_log(sid: str) -> list[dict]:
    return relay.SESSIONS[sid]["log"]


# --- relay rules ------------------------------------------------------------


@pytest.fixture
def rc():
    relay.SESSIONS.clear()
    relay.CODES.clear()
    return TestClient(relay.app)


def _accepted(rc) -> str:
    s = rc.post("/sessions", json={"mode": "count"}).json()
    rc.post("/sessions/join", json={"invite_code": s["invite_code"]})
    rc.post(f"/sessions/{s['session_id']}/accept")
    return s["session_id"]


def _post(rc, sid, n, role, payload=None):
    return rc.post(f"/sessions/{sid}/round/{n}",
                   json={"role": role, "payload": payload or {"h": "ab" * 32}})


def test_relay_create_and_join(rc):
    s = rc.post("/sessions", json={"mode": "detail"}).json()
    assert re.fullmatch(r"[0-9a-f]{32}", s["session_id"])
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{8}", s["invite_code"])
    j = rc.post("/sessions/join", json={"invite_code": s["invite_code"].lower()})
    assert j.json() == {"session_id": s["session_id"], "mode": "detail"}
    assert rc.post("/sessions/join", json={"invite_code": s["invite_code"]}).status_code == 409
    assert rc.post("/sessions/join", json={"invite_code": "ZZZZZZZZ"}).status_code == 404
    assert rc.get(f"/sessions/{s['session_id']}/state").json()["state"] == "joined"


def test_relay_accept_decline_fail_transitions(rc):
    s = rc.post("/sessions", json={"mode": "count"}).json()
    sid = s["session_id"]
    assert rc.post(f"/sessions/{sid}/accept").status_code == 409  # not joined yet
    rc.post("/sessions/join", json={"invite_code": s["invite_code"]})
    assert rc.post(f"/sessions/{sid}/decline").status_code == 200
    assert rc.get(f"/sessions/{sid}/state").json()["state"] == "declined"
    assert rc.post(f"/sessions/{sid}/fail", json={"role": "initiator"}).status_code == 409
    sid2 = _accepted(rc)
    assert rc.post(f"/sessions/{sid2}/fail", json={"role": "joiner"}).status_code == 200
    assert rc.get(f"/sessions/{sid2}/state").json()["state"] == "failed"


def test_relay_refuses_rounds_before_accept(rc):
    s = rc.post("/sessions", json={"mode": "count"}).json()
    assert _post(rc, s["session_id"], 0, "initiator").status_code == 409
    rc.post("/sessions/join", json={"invite_code": s["invite_code"]})
    assert _post(rc, s["session_id"], 0, "joiner").status_code == 409
    assert relay.SESSIONS[s["session_id"]]["log"] == []


def test_relay_no_double_post_no_skip_and_bad_round(rc):
    sid = _accepted(rc)
    assert _post(rc, sid, 1, "initiator").status_code == 409  # skipped round 0
    assert _post(rc, sid, 0, "initiator").status_code == 200
    assert _post(rc, sid, 0, "initiator").status_code == 409  # double post
    assert _post(rc, sid, 3, "initiator").status_code == 422
    assert rc.get(f"/sessions/{sid}/round/3", params={"role": "joiner"}).status_code == 422


def test_relay_release_rules(rc):
    sid = _accepted(rc)
    get = lambda n, role: rc.get(f"/sessions/{sid}/round/{n}", params={"role": role})  # noqa: E731
    _post(rc, sid, 0, "initiator", {"h": "11" * 32})
    assert get(0, "joiner").status_code == 204  # only one commitment in
    # Initiator reveals early; joiner must not see it before committing.
    _post(rc, sid, 1, "initiator", {"blinded": ["22" * 32]})
    assert get(1, "joiner").status_code == 204
    _post(rc, sid, 0, "joiner", {"h": "33" * 32})
    assert get(0, "joiner").json() == {"h": "11" * 32}
    assert get(0, "initiator").json() == {"h": "33" * 32}
    assert get(1, "joiner").json() == {"blinded": ["22" * 32]}
    assert get(1, "initiator").status_code == 204
    _post(rc, sid, 1, "joiner", {"blinded": ["44" * 32]})
    _post(rc, sid, 2, "initiator", {"points": ["55" * 32]})
    assert get(2, "joiner").json() == {"points": ["55" * 32]}
    assert rc.get(f"/sessions/{sid}/state").json()["state"] == "accepted"
    _post(rc, sid, 2, "joiner", {"points": ["66" * 32]})
    assert rc.get(f"/sessions/{sid}/state").json()["state"] == "done"


def test_relay_log_prefixes_only(rc):
    sid = _accepted(rc)
    _post(rc, sid, 0, "initiator", {"h": "ab" * 32})
    _post(rc, sid, 0, "joiner", {"h": "cd" * 32})
    _post(rc, sid, 1, "initiator", {"blinded": ["ef" * 32, "01" * 32], "person_id": "alex",
                                    "count": 2, "commitment": "99" * 32})
    entries = rc.get(f"/sessions/{sid}/log").json()
    assert [e["kind"] for e in entries] == ["commitment", "commitment", "blinded_set"]
    assert entries[2]["prefixes"] == ["efefefef", "01010101"]
    assert all(HEX8.match(p) for e in entries for p in e["prefixes"])
    assert "alex" not in json.dumps(entries) and "9999" not in json.dumps(entries)


def test_relay_sessions_expire_after_24h(rc):
    s = rc.post("/sessions", json={"mode": "count"}).json()
    relay.SESSIONS[s["session_id"]]["created"] -= 25 * 3600
    assert rc.get(f"/sessions/{s['session_id']}/state").status_code == 404
    assert rc.post("/sessions/join", json={"invite_code": s["invite_code"]}).status_code == 404
    assert s["session_id"] not in relay.SESSIONS


# --- lab --------------------------------------------------------------------


def test_lab_keys_persist_and_are_private(tmp_path):
    path = tmp_path / "lab_keys.json"
    assert keys.load_or_create(path) == keys.load_or_create(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_lab_service(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    from lab.service import app
    c = TestClient(app)
    pub = c.get("/pubkey").json()["ed25519_pubkey"]
    assert re.fullmatch(r"[0-9a-f]{64}", pub)
    holder = crypto.pubkey_from_seed(b"\x01" * 32).hex()
    r = c.post("/credentials", json={"person_id": "alex", "genes": ["PAH", "CFTR"],
                                     "findings": [{"gene": "BRCA1", "finding": CLAIM_FINDING}],
                                     "holder_pubkey": holder})
    assert r.status_code == 200
    body = r.json()
    assert body["claims"][0]["gene"] == "BRCA1"
    Session("initiator", "count", "s1", body["credential"], ["CFTR", "PAH"], pub)
    bad = c.post("/credentials", json={"person_id": "x", "genes": ["NOTAGENE"],
                                       "holder_pubkey": holder})
    assert bad.status_code == 400


# --- seeding ----------------------------------------------------------------


def test_seed_writes_valid_wallets(var):
    files = sorted((var / "wallets").glob("*.json"))
    assert {f.stem for f in files} == set(seed_demo.PEOPLE)
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
        w = json.loads(f.read_text())
        assert w["genes"] == sorted(seed_demo.PEOPLE[w["person_id"]][0])
        Session("initiator", "count", "s1", w["credential"], w["genes"], w["lab_pubkey"])
    claims = {f.stem: [c["gene"] for c in json.loads(f.read_text())["claims"]] for f in files}
    assert claims["alex"] == ["BRCA1"] and claims["morgan"] == ["BRCA2"]
    assert claims["sam"] == []


# --- wallet: gene matching --------------------------------------------------


def test_wallet_me_and_idle(var):
    async def go():
        w = wallet_client("alex", relay_client())
        me = (await w.get("/me")).json()
        assert me == {"person_id": "alex", "genes": ["CFTR", "GBA", "PAH"], "count": 3,
                      "claims": [{"gene": "BRCA1", "finding": CLAIM_FINDING}]}
        assert (await w.get("/check/status")).json()["state"] == "idle"
    asyncio.run(go())


@pytest.mark.parametrize("couple", [1, 2, 3])
@pytest.mark.parametrize("mode", ["count", "detail"])
def test_couples_match(var, couple, mode):
    a, b = seed_demo.COUPLES[couple]
    sa, sb, *_ = asyncio.run(run_check(a, b, mode))
    assert sa["state"] == sb["state"] == "done", (sa, sb)
    assert sa["result"] == sb["result"]
    assert sa["result"]["shared_count"] == len(EXPECTED[couple])
    if mode == "detail":
        assert sa["result"]["shared_genes"] == EXPECTED[couple]
    else:
        assert sa["result"]["shared_genes"] is None
    assert sa["error"] is None and sb["error"] is None


def test_count_mode_reveals_no_gene_names(var):
    async def go():
        sa, sb, wa, wb, sid = await run_check("casey", "morgan", "count")
        seen = [(await w.get("/check/seen")).json() for w in (wa, wb)]
        return json.dumps([sa, sb, seen, relay_log(sid)])
    blob = asyncio.run(go())
    assert not [g for g in PANEL_GENES if g in blob]


def test_seen_shows_only_prefixes_and_counts(var):
    async def go():
        _, _, wa, _, sid = await run_check("alex", "sam", "detail")
        return (await wa.get("/check/seen")).json(), sid
    seen, sid = asyncio.run(go())
    parties = {p["party"]: p["items"] for p in seen["parties"]}
    assert set(parties) == {"me", "partner", "relay", "agent"}
    assert len(parties["relay"]) == 6 and len(relay_log(sid)) == 6

    def ok(v):
        return isinstance(v, int) or (isinstance(v, str) and HEX8.match(v)) or (
            isinstance(v, list) and all(isinstance(x, str) and HEX8.match(x) for x in v))
    for party in ("partner", "relay"):
        assert parties[party] and all(ok(i["value"]) for i in parties[party]), party
    assert all(ok(i["value"]) for i in parties["me"] if i["label"] != "result")
    assert parties["agent"] == []  # no counselor installed: nothing sent to the agent


@pytest.mark.parametrize("mode", ["count", "detail"])
def test_counselor_gets_only_the_result(var, monkeypatch, mode):
    calls = []
    fake = types.ModuleType("agent.counselor")
    fake.explain = lambda result, panel: calls.append(result) or {"summary": "ok"}
    monkeypatch.setitem(sys.modules, "agent.counselor", fake)

    async def go():
        sa, _, wa, _, _ = await run_check("casey", "morgan", mode)
        return sa, (await wa.get("/check/seen")).json()
    sa, seen = asyncio.run(go())
    agent = next(p["items"] for p in seen["parties"] if p["party"] == "agent")
    assert sa["explanation"] == {"summary": "ok"}
    if mode == "count":
        assert agent == [{"label": "counselor input", "value": {"shared_count": 2}}]
        assert calls[0].shared_genes is None
    else:
        assert [e["gene"] for e in agent[0]["value"]["shared"]] == ["HBB", "SMN1"]


@pytest.mark.parametrize("decline_after_r0", [False, True])
def test_wallet_zeroes_scalar_on_completion_and_abort(var, decline_after_r0):
    async def go():
        rc = relay_client()
        app_a, app_b = create_app("alex", rc), create_app("sam", rc)
        wa = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_a), base_url="http://a")
        wb = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_b), base_url="http://b")
        s = (await wa.post("/check/start", json={"mode": "count"})).json()
        await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        if decline_after_r0:
            # Joiner never runs; the relay marks the session failed after Alex commits.
            relay.SESSIONS[s["session_id"]]["state"] = "accepted"
            while app_a.state.holder["check"]["round"] != 0 or not relay_log(s["session_id"]):
                await asyncio.sleep(0.005)
            relay.SESSIONS[s["session_id"]]["state"] = "failed"
        else:
            await wb.post("/check/accept")
            await wait_terminal(wb)
        st = await wait_terminal(wa)
        return st, app_a.state.holder["check"]["session"]
    st, session = asyncio.run(go())
    assert st["state"] == ("failed" if decline_after_r0 else "done")
    assert session.scalar_is_zeroed()


# --- wallet: failure paths --------------------------------------------------


def test_decline_stops_initiator_and_nothing_is_exchanged(var):
    async def go():
        rc = relay_client()
        wa, wb = wallet_client("alex", rc), wallet_client("sam", rc)
        s = (await wa.post("/check/start", json={"mode": "detail"})).json()
        await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        await asyncio.sleep(0.05)
        assert relay_log(s["session_id"]) == []  # joined, not accepted: nothing sent
        assert (await wb.post("/check/decline")).json() == {"ok": True}
        return await wait_terminal(wa), await wait_terminal(wb), s["session_id"]
    sa, sb, sid = asyncio.run(go())
    assert sa["state"] == "failed" and sa["error"]["code"] == "PEER_DECLINED"
    assert sb["state"] == "declined"
    assert relay_log(sid) == []


def test_nothing_sent_before_accept(var):
    async def go():
        rc = relay_client()
        wa, wb = wallet_client("alex", rc), wallet_client("sam", rc)
        s = (await wa.post("/check/start", json={"mode": "count"})).json()
        j = (await wb.post("/check/join", json={"invite_code": s["invite_code"]})).json()
        await asyncio.sleep(0.05)
        st_a = (await wa.get("/check/status")).json()["state"]
        st_b = (await wb.get("/check/status")).json()["state"]
        return j, st_a, st_b, s["session_id"]
    j, st_a, st_b, sid = asyncio.run(go())
    assert j["mode"] == "count" and "how many" in j["mode_text"]
    assert (st_a, st_b) == ("waiting_for_partner", "awaiting_accept")
    assert relay_log(sid) == [] and relay.SESSIONS[sid]["rounds"] == {}


def test_invalid_and_reused_invite_codes(var):
    async def go():
        rc = relay_client()
        wa, wb, wc = (wallet_client(p, rc) for p in ("alex", "sam", "jordan"))
        s = (await wa.post("/check/start", json={"mode": "count"})).json()
        first = await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        reused = await wc.post("/check/join", json={"invite_code": s["invite_code"]})
        unknown = await wc.post("/check/join", json={"invite_code": "00000000"})
        return first, reused, unknown, (await wc.get("/check/status")).json()
    first, reused, unknown, st = asyncio.run(go())
    assert first.status_code == 200
    for r in (reused, unknown):
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_INVITE"
    assert st["state"] == "idle"


@pytest.mark.parametrize("field", ["lab_sig", "token"])
def test_tampered_wallet_sends_nothing(var, field):
    path = var / "wallets" / "sam.json"
    w = json.loads(path.read_text())
    if field == "lab_sig":
        sig = w["credential"]["lab_sig"]
        w["credential"]["lab_sig"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    else:  # swap in a different valid point, so only the commitment check catches it
        w["credential"]["tokens"][0] = crypto.hash_to_group("TAMPER").hex()
    path.write_text(json.dumps(w))

    sa, sb, *_, sid = asyncio.run(run_check("alex", "sam", "detail"))
    assert sb["state"] == "failed" and sb["error"]["code"] == "BAD_CREDENTIAL"
    assert sa["state"] == "failed" and sa["result"] is None
    assert not [e for e in relay_log(sid) if e["role"] == "joiner"]


def test_forged_partner_is_rejected(var):
    # Sam's credential comes from a different lab key. His own wallet trusts that key,
    # so his self-check passes, but Alex verifies against the real lab and refuses.
    rogue = Lab()
    seed_demo.write_wallet(var, "sam", ["CFTR", "HEXA"], [], rogue.k_lab, rogue.seed,
                           keys.load_panel())
    sa, sb, *_, sid = asyncio.run(run_check("alex", "sam", "detail"))
    assert sa["state"] == "failed" and sa["error"]["code"] == "BAD_SIGNATURE"
    assert sa["result"] is None and sb["result"] is None
    assert not [e for e in relay_log(sid) if e["round"] == 2]


def test_relay_unavailable(var):
    def down(request):
        raise httpx.ConnectError("relay down")
    dead = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://relay")

    async def go():
        w = wallet_client("alex", dead)
        return await w.post("/check/start", json={"mode": "count"})
    r = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == "RELAY_UNAVAILABLE"


def test_accept_without_join_is_refused(var):
    async def go():
        w = wallet_client("sam", relay_client())
        return await w.post("/check/accept")
    r = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == "OUT_OF_ORDER"


# --- demo script ------------------------------------------------------------


def test_demo_backend_script_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_backend.py")],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: all checks passed" in r.stdout
