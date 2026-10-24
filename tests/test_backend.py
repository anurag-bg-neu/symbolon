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
from tests.helpers import Lab, signed_in_client

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)

from wallet.service import create_app  # noqa: E402

PANEL_GENES = {e["gene"] for e in keys.load_panel()}
EXPECTED = {1: ["CFTR"], 2: [], 3: ["HBB", "SMN1"]}
HEX8 = re.compile(r"^[0-9a-f]{8}$")


def assert_owner_only(path: Path) -> None:
    """chmod 600 on macOS/Linux. Windows has no POSIX modes (chmod only toggles
    read-only), so there we only check the file exists and is readable."""
    assert path.is_file() and path.read_bytes()
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
    return signed_in_client(create_app(person_id, rc), f"http://{person_id}")


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
    # Round 2 waits for both sides, so the joiner cannot copy or reorder the
    # initiator's double-blinded points into its own round 2.
    assert get(2, "joiner").status_code == 204
    assert get(2, "initiator").status_code == 204
    assert rc.get(f"/sessions/{sid}/state").json()["state"] == "accepted"
    _post(rc, sid, 2, "joiner", {"points": ["66" * 32]})
    assert get(2, "joiner").json() == {"points": ["55" * 32]}
    assert get(2, "initiator").json() == {"points": ["66" * 32]}
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
    # Round 1 also lists the signed metadata the relay received (Req 7.1):
    # IDs and counts as-is, hashes and signatures as 8-hex prefixes only.
    assert entries[2]["meta"] == {"person_id": "alex", "count": 2, "commitment": "99999999"}
    assert "meta" not in entries[0] and "99" * 32 not in json.dumps(entries)


@pytest.mark.parametrize("payload,code", [
    ({"blinded": ["ab" * 32] * 21}, 413),          # more than MAX_SET points
    ({"h": "ab" * 200}, 413),                      # oversized string
    ({f"k{i}": 1 for i in range(9)}, 413),         # too many fields
    ({"blinded": [{"x": 1}]}, 422),                # nested objects
    ({"h": {"x": "ab"}}, 422),
    ({"h": None}, 422),
])
def test_relay_bounds_round_payloads(rc, payload, code):
    sid = _accepted(rc)
    assert _post(rc, sid, 0, "initiator", payload).status_code == code
    assert relay.SESSIONS[sid]["rounds"] == {} and relay.SESSIONS[sid]["log"] == []


def test_relay_rejects_oversized_body(rc):
    sid = _accepted(rc)
    big = json.dumps({"role": "initiator", "payload": {"h": "a" * 20_000}})
    r = rc.post(f"/sessions/{sid}/round/0", content=big,
                headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and relay.SESSIONS[sid]["rounds"] == {}


def test_relay_caps_live_sessions(rc, monkeypatch):
    monkeypatch.setattr(relay, "MAX_SESSIONS", 2)
    assert rc.post("/sessions", json={"mode": "count"}).status_code == 200
    assert rc.post("/sessions", json={"mode": "count"}).status_code == 200
    assert rc.post("/sessions", json={"mode": "count"}).status_code == 503


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
    assert_owner_only(path)


def test_lab_issuance_closed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.delenv("GENEMATCH_LAB_OPEN", raising=False)
    from lab.service import app
    c = TestClient(app)
    r = c.post("/credentials", json={"person_id": "mallory", "genes": sorted(PANEL_GENES)[:8],
                                     "holder_pubkey": "11" * 32})
    assert r.status_code == 403 and "credential" not in r.text
    assert c.get("/pubkey").status_code == 200  # the public key stays public


def test_lab_service(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setenv("GENEMATCH_LAB_OPEN", "1")
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
        assert_owner_only(f)
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
                      "claims": [{"gene": "BRCA1", "finding": CLAIM_FINDING}],
                      "display_name": "Alex", "synthetic": True, "has_results": True,
                      "lab_portal": "http://localhost:8200"}
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
    assert set(parties) == {"me", "partner", "relay", "agent", "trial"}
    assert parties["trial"] == []  # no presentations sent
    assert len(relay_log(sid)) == 6
    # 6 round entries plus 5 signed-metadata items for each side's round 1.
    assert len(parties["relay"]) == 6 + 2 * 5

    def ok(v):
        return isinstance(v, int) or (isinstance(v, str) and HEX8.match(v)) or (
            isinstance(v, list) and all(isinstance(x, str) and HEX8.match(x) for x in v))

    def meta_ok(item):
        # Plain person ID and issue time are what the parties really receive.
        if item["label"].startswith("person ID"):
            return item["value"] in ("alex", "sam")
        if item["label"].startswith("issue time"):
            return re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", item["value"])
        return ok(item["value"])
    for party in ("partner", "relay"):
        assert parties[party] and all(meta_ok(i) for i in parties[party]), party
    assert all(meta_ok(i) for i in parties["me"] if i["label"] != "result")
    # Every party view names the signed metadata, not just the points.
    for party, who in (("partner", "from you"), ("me", "from your partner")):
        labels = {i["label"] for i in parties[party]}
        assert {f"{lbl} {who}" for lbl in ("person ID", "carrier count", "lab signature",
                                           "credential commitment")} <= labels, party
    assert {"person ID from initiator", "person ID from joiner"} <= {
        i["label"] for i in parties["relay"]}
    # No full hashes or signatures anywhere in the panel.
    blob = json.dumps(seen)
    assert not re.search(r"[0-9a-f]{16,}", blob)
    # The template counselor is installed: in Detail mode it receives only the
    # panel entries for the agreed shared genes (CFTR for alex/sam), nothing else.
    agent_input = parties["agent"][0]["value"] if parties["agent"] else None
    if agent_input is not None:
        assert [e["gene"] for e in agent_input["shared"]] == ["CFTR"]


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
        wa = signed_in_client(app_a, "http://a")
        wb = signed_in_client(app_b, "http://b")
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
    # Consent text discloses that each side learns the other's total carrier count.
    assert "in total" in j["mode_text"]
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
    # Alex is told his partner stopped the check, not that it expired.
    assert sa["state"] == "failed" and sa["result"] is None
    assert sa["error"]["code"] == "PEER_FAILED"
    assert not [e for e in relay_log(sid) if e["role"] == "joiner"]


def test_forged_partner_is_rejected(var):
    # Sam's credential comes from a different lab key. His own wallet trusts that key,
    # so his self-check passes, but Alex verifies against the real lab and refuses.
    rogue = Lab()
    seed_demo.write_wallet(var, "sam", ["CFTR", "HEXA"], [], rogue.k_lab, rogue.seed,
                           keys.load_panel())
    sa, sb, *_, sid = asyncio.run(run_check("alex", "sam", "detail"))
    # Each side rejects the other's lab signature; whoever checks first reports
    # BAD_SIGNATURE and tells the relay, and the other may then see PEER_FAILED.
    codes = {sa["error"]["code"], sb["error"]["code"]}
    assert sa["state"] == sb["state"] == "failed"
    assert "BAD_SIGNATURE" in codes and codes <= {"BAD_SIGNATURE", "PEER_FAILED"}
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


@pytest.mark.parametrize("status", [403, 409, 422])
def test_start_reports_relay_refusal(var, status):
    refusing = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json={})),
        base_url="http://relay")

    async def go():
        w = wallet_client("alex", refusing)
        return await w.post("/check/start", json={"mode": "count"}), \
            (await w.get("/check/status")).json()["state"]
    r, state = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == "RELAY_UNAVAILABLE"
    assert state == "idle"


def test_mode_text_discloses_carrier_count():
    from wallet.service import MODE_TEXT
    for text in MODE_TEXT.values():
        assert "how many carrier genes the other person has in total" in text


def test_accept_without_join_is_refused(var):
    async def go():
        w = wallet_client("sam", relay_client())
        return await w.post("/check/accept")
    r = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == "OUT_OF_ORDER"


def test_relay_stops_releasing_rounds_after_failure(rc):
    sid = _accepted(rc)
    _post(rc, sid, 0, "initiator")
    _post(rc, sid, 0, "joiner")
    assert rc.get(f"/sessions/{sid}/round/0", params={"role": "joiner"}).status_code == 200
    rc.post(f"/sessions/{sid}/fail", json={"role": "joiner"})
    assert rc.get(f"/sessions/{sid}/round/0", params={"role": "joiner"}).status_code == 409


@pytest.mark.parametrize("headers,allowed", [
    ({}, True),                                               # curl, tests, server-side
    ({"Origin": "http://sam"}, True),                         # the wallet's own page
    ({"Origin": "https://evil.example"}, False),              # another website
    ({"Origin": "http://localhost:8300"}, False),             # another local service
    ({"Origin": "null"}, False),                              # sandboxed iframe, file://
    ({"Sec-Fetch-Site": "cross-site"}, False),                # no Origin, still cross-site
])
def test_wallet_refuses_cross_origin_posts(var, headers, allowed):
    async def go():
        rc = relay_client()
        wa, wb = wallet_client("alex", rc), wallet_client("sam", rc)
        s = (await wa.post("/check/start", json={"mode": "detail"})).json()
        await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        # A form-style "simple" request: no JSON, so no CORS preflight in a browser.
        r = await wb.post("/check/accept", headers={**headers, "Content-Type": "text/plain"})
        return r, (await wb.get("/check/status")).json()["state"], s["session_id"]
    r, state, sid = asyncio.run(go())
    if allowed:
        assert r.status_code == 200 and state == "running"
    else:
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
        assert state == "awaiting_accept" and relay.SESSIONS[sid]["state"] == "joined"


def test_counselor_failure_does_not_break_the_result(var, monkeypatch):
    fake = types.ModuleType("agent.counselor")

    def boom(result, panel):
        raise RuntimeError("model unavailable")
    fake.explain = boom
    monkeypatch.setitem(sys.modules, "agent.counselor", fake)
    sa, sb, *_ = asyncio.run(run_check("casey", "morgan", "detail"))
    assert sa["state"] == "done" and sa["result"]["shared_genes"] == ["HBB", "SMN1"]
    assert sa["explanation"] is None


# --- demo script ------------------------------------------------------------


def test_demo_backend_script_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_backend.py")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: all checks passed" in r.stdout
