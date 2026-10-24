"""Task 7.6: trial verifier and wallet presentation, over in-process HTTP.

Covers valid presentation, reused and expired nonces, tampered claim, wrong holder
key, wrong finding, no matching claim, and that the wallet sends exactly one claim.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import secrets
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from core import crypto
from core.claims import make_presentation
from core.models import CLAIM_FINDING, SignedClaim
from lab import keys
from relay import service as relay
from trial import service as trial_service
from tests.helpers import signed_in_client
from wallet.service import create_app as create_wallet

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)

BRCA1_TRIAL = "parp-demo-01"
BRCA2_TRIAL = "parp-demo-02"


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Seeded wallets, a trial app on a fake clock, and the lab public key."""
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setattr(trial_service, "TRIALS_FILE", tmp_path / "no-trials.json")
    seed_demo.seed(tmp_path)
    alex = json.loads((tmp_path / "wallets" / "alex.json").read_text())
    clock = Clock()
    app = trial_service.create_app(lab_pubkey=alex["lab_pubkey"], clock=clock)
    return {"var": tmp_path, "app": app, "clock": clock, "lab_pubkey": alex["lab_pubkey"]}


def wallet_file(env, person: str) -> dict:
    return json.loads((env["var"] / "wallets" / f"{person}.json").read_text())


def tc(env) -> TestClient:
    return TestClient(env["app"])


def new_request(c: TestClient, trial_id: str = BRCA1_TRIAL) -> dict:
    r = c.post("/requests", json={"trial_id": trial_id})
    assert r.status_code == 200, r.text
    return r.json()


def presentation_for(env, person: str, req: dict, claim_index: int = 0) -> dict:
    w = wallet_file(env, person)
    claim = SignedClaim.model_validate(w["claims"][claim_index])
    return make_presentation(claim, bytes.fromhex(w["holder_seed"]),
                             req["nonce"], req["trial_id"]).model_dump()


def present(c: TestClient, req: dict, presentation: dict) -> dict:
    r = c.post(f"/requests/{req['request_id']}/present", json={"presentation": presentation})
    assert r.status_code == 200, r.text
    return r.json()


# --- trial service -----------------------------------------------------------


def test_trials_fallback_lists_brca_trials(env):
    trials = tc(env).get("/trials").json()
    assert {t["required"]["gene"] for t in trials} == {"BRCA1", "BRCA2"}
    assert all(t["required"]["finding"] == CLAIM_FINDING for t in trials)


def test_trials_file_is_used_and_filtered(env, tmp_path, monkeypatch):
    f = tmp_path / "trials.json"
    f.write_text(json.dumps([
        {"trial_id": "t1", "name": "One", "summary": "s",
         "required": {"gene": "BRCA2", "finding": CLAIM_FINDING}},
        {"trial_id": "t2", "name": "Tumor", "summary": "s",
         "required": {"gene": "KRAS", "finding": "somatic mutation"}},
    ]))
    monkeypatch.setattr(trial_service, "TRIALS_FILE", f)
    assert [t["trial_id"] for t in tc(env).get("/trials").json()] == ["t1"]


def test_new_request_shape(env):
    c = tc(env)
    req = new_request(c)
    assert len(req["request_id"]) == 32 and len(req["nonce"]) == 32
    assert req["gene"] == "BRCA1" and req["finding"] == CLAIM_FINDING
    assert req["expires_at"].endswith("Z")
    assert new_request(c)["nonce"] != req["nonce"]
    st = c.get(f"/requests/{req['request_id']}").json()
    assert st["status"] == "pending" and st["received"]["claims"] == 0


def test_unknown_trial_and_request(env):
    c = tc(env)
    assert c.post("/requests", json={"trial_id": "nope"}).status_code == 404
    assert c.get("/requests/" + "0" * 32).status_code == 404


def test_valid_presentation_eligible(env):
    c = tc(env)
    req = new_request(c)
    out = present(c, req, presentation_for(env, "alex", req))
    assert out == {"status": "eligible", "code": None,
                   "reason": "Eligible — verified by lab"}
    st = c.get(f"/requests/{req['request_id']}").json()
    assert st["status"] == "eligible"
    assert st["received"] == {"claims": 1, "gene": "BRCA1", "finding": CLAIM_FINDING}


def test_reused_nonce_rejected(env):
    c = tc(env)
    req = new_request(c)
    p = presentation_for(env, "alex", req)
    assert present(c, req, p)["status"] == "eligible"
    again = present(c, req, p)
    assert again["status"] == "rejected" and again["code"] == "NONCE_REUSED"
    # A replay does not overwrite the first, valid outcome.
    assert c.get(f"/requests/{req['request_id']}").json()["status"] == "eligible"


def test_expired_nonce_rejected(env):
    c = tc(env)
    req = new_request(c)
    env["clock"].t += 5 * 60 + 1
    st = c.get(f"/requests/{req['request_id']}").json()
    assert st["status"] == "rejected" and st["code"] == "NONCE_EXPIRED"
    out = present(c, req, presentation_for(env, "alex", req))
    assert out["code"] == "NONCE_EXPIRED"


def test_not_expired_just_before_deadline(env):
    c = tc(env)
    req = new_request(c)
    env["clock"].t += 5 * 60 - 1
    assert present(c, req, presentation_for(env, "alex", req))["status"] == "eligible"


@pytest.mark.parametrize("field,value", [("finding", "something else"), ("salt", "00" * 16),
                                         ("person_id", "mallory")])
def test_tampered_claim_rejected(env, field, value):
    c = tc(env)
    req = new_request(c)
    p = presentation_for(env, "alex", req)
    p["claim"][field] = value
    out = present(c, req, p)
    assert out["status"] == "rejected" and out["code"] == "BAD_LAB_SIG"


def test_copied_claim_with_wrong_holder_key(env):
    """Sam copies Alex's claim and signs the challenge with his own key."""
    c = tc(env)
    req = new_request(c)
    claim = SignedClaim.model_validate(wallet_file(env, "alex")["claims"][0])
    sam_seed = bytes.fromhex(wallet_file(env, "sam")["holder_seed"])
    from core.claims import _present_body
    sig = crypto.sign(sam_seed, crypto.PRESENT, _present_body(claim, req["nonce"], req["trial_id"]))
    p = {"claim": claim.model_dump(), "trial_id": req["trial_id"], "nonce": req["nonce"],
         "holder_sig": sig}
    assert present(c, req, p)["code"] == "BAD_HOLDER_SIG"


def test_presentation_for_another_request(env):
    c = tc(env)
    old, new = new_request(c), new_request(c)
    out = present(c, new, presentation_for(env, "alex", old))
    assert out["code"] == "REQUEST_MISMATCH"


def test_wrong_finding_rejected(env):
    """Morgan's BRCA2 claim answering a BRCA1 trial."""
    c = tc(env)
    req = new_request(c, BRCA1_TRIAL)
    out = present(c, req, presentation_for(env, "morgan", req))
    assert out["code"] == "WRONG_FINDING"


def test_failed_attempt_uses_up_the_nonce(env):
    c = tc(env)
    req = new_request(c)
    bad = presentation_for(env, "alex", req)
    bad["holder_sig"] = "00" * 64
    assert present(c, req, bad)["code"] == "BAD_HOLDER_SIG"
    assert present(c, req, presentation_for(env, "alex", req))["code"] == "NONCE_REUSED"


def test_malformed_presentation_rejected(env):
    c = tc(env)
    req = new_request(c)
    out = present(c, req, {"claim": {}, "nonce": "x"})
    assert out["status"] == "rejected"


def test_wrong_lab_key_rejected(env, tmp_path):
    other = trial_service.create_app(lab_pubkey=crypto.pubkey_from_seed(b"\x02" * 32).hex(),
                                     clock=env["clock"])
    c = TestClient(other)
    req = new_request(c)
    assert present(c, req, presentation_for(env, "alex", req))["code"] == "BAD_LAB_SIG"


# --- wallet <-> trial over in-process HTTP -----------------------------------


def clients(env, person: str):
    trial_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=env["app"]),
                                base_url="http://trial")
    relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                base_url="http://relay")
    w = signed_in_client(create_wallet(person, relay_c, trial_c), f"http://{person}")
    return w, trial_c


def capture(env):
    """Record exactly what the wallet posts to the trial."""
    sent: list[dict] = []
    real = env["app"]

    async def spy(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].endswith("/present"):
            body = b""
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            sent.append(json.loads(body))

            async def replay():
                return {"type": "http.request", "body": body, "more_body": False}
            return await real(scope, replay, send)
        return await real(scope, receive, send)
    return spy, sent


def test_wallet_end_to_end_eligible_and_sends_one_claim(env):
    spy, sent = capture(env)

    async def go():
        trial_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=spy), base_url="http://trial")
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                    base_url="http://relay")
        w = signed_in_client(create_wallet("alex", relay_c, trial_c), "http://alex")
        req = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        pending = (await w.get("/present/pending", params={"request_id": req["request_id"]})).json()
        out = (await w.post("/present", json={"request_id": req["request_id"],
                                               "approve": True})).json()
        status = (await trial_c.get(f"/requests/{req['request_id']}")).json()
        seen = (await w.get("/check/seen")).json()
        return pending, out, status, seen
    pending, out, status, seen = asyncio.run(go())

    assert pending == {"request_id": pending["request_id"],
                       "trial_name": "PARP inhibitor study (demo)", "gene": "BRCA1",
                       "finding": CLAIM_FINDING, "status": "pending",
                       "has_matching_claim": True}
    assert out["status"] == "eligible"
    assert status["status"] == "eligible" and status["received"]["claims"] == 1

    # Exactly one presentation, with exactly one claim and nothing else.
    assert len(sent) == 1
    body = sent[0]
    assert set(body) == {"presentation"}
    assert set(body["presentation"]) == {"claim", "trial_id", "nonce", "holder_sig"}
    assert set(body["presentation"]["claim"]) == set(SignedClaim.model_fields)
    text = json.dumps(body)
    alex = wallet_file(env, "alex")
    assert not any(t in text for t in alex["credential"]["tokens"])
    assert alex["credential"]["commitment"] not in text
    assert alex["holder_seed"] not in text
    assert not any(g in text for g in alex["genes"])

    trial_items = next(p["items"] for p in seen["parties"] if p["party"] == "trial")
    assert {"label": f"claims shared with {BRCA1_TRIAL}", "value": 1} in trial_items


def test_wallet_with_no_matching_claim_sends_nothing(env):
    spy, sent = capture(env)

    async def go():
        trial_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=spy), base_url="http://trial")
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                    base_url="http://relay")
        w = signed_in_client(create_wallet("sam", relay_c, trial_c), "http://sam")
        req = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        pending = (await w.get("/present/pending", params={"request_id": req["request_id"]})).json()
        out = (await w.post("/present", json={"request_id": req["request_id"],
                                               "approve": True})).json()
        return pending, out
    pending, out = asyncio.run(go())
    assert pending["has_matching_claim"] is False
    assert out["status"] == "no_matching_claim" and out["code"] == "NO_MATCHING_CLAIM"
    assert sent == []


def test_wallet_decline_sends_nothing(env):
    spy, sent = capture(env)

    async def go():
        trial_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=spy), base_url="http://trial")
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                    base_url="http://relay")
        w = signed_in_client(create_wallet("alex", relay_c, trial_c), "http://alex")
        req = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        return (await w.post("/present", json={"request_id": req["request_id"],
                                                "approve": False})).json()
    out = asyncio.run(go())
    assert out["status"] == "declined" and sent == []


def test_wallet_morgan_eligible_for_brca2_only(env):
    async def go():
        w, trial_c = clients(env, "morgan")
        r1 = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        r2 = (await trial_c.post("/requests", json={"trial_id": BRCA2_TRIAL})).json()
        o1 = (await w.post("/present", json={"request_id": r1["request_id"], "approve": True})).json()
        o2 = (await w.post("/present", json={"request_id": r2["request_id"], "approve": True})).json()
        return o1, o2
    o1, o2 = asyncio.run(go())
    assert o1["status"] == "no_matching_claim"
    assert o2["status"] == "eligible"


def test_wallet_second_present_is_not_resent(env):
    async def go():
        w, trial_c = clients(env, "alex")
        req = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        body = {"request_id": req["request_id"], "approve": True}
        first = (await w.post("/present", json=body)).json()
        second = (await w.post("/present", json=body)).json()
        return first, second
    first, second = asyncio.run(go())
    assert first["status"] == "eligible"
    assert second["status"] == "eligible"  # wallet reports the outcome, does not resend


@pytest.mark.parametrize("request_id", ["../trials", "ABC", "0" * 31, "g" * 32])
def test_wallet_rejects_bad_request_ids(env, request_id):
    async def go():
        w, _ = clients(env, "alex")
        return await w.get("/present/pending", params={"request_id": request_id})
    r = asyncio.run(go())
    assert r.status_code == 400 and r.json()["error"]["code"] == "UNKNOWN_REQUEST"


def test_wallet_unknown_request(env):
    async def go():
        w, _ = clients(env, "alex")
        return await w.get("/present/pending", params={"request_id": "a" * 32})
    r = asyncio.run(go())
    assert r.status_code == 404 and r.json()["error"]["code"] == "UNKNOWN_REQUEST"


def test_wallet_trial_unavailable(env):
    def down(request):
        raise httpx.ConnectError("trial down")

    async def go():
        dead = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://trial")
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                    base_url="http://relay")
        w = signed_in_client(create_wallet("alex", relay_c, dead), "http://alex")
        return await w.post("/present", json={"request_id": "a" * 32, "approve": True})
    r = asyncio.run(go())
    assert r.status_code == 502 and r.json()["error"]["code"] == "TRIAL_UNAVAILABLE"


def test_wallet_present_refuses_cross_origin(env):
    async def go():
        w, trial_c = clients(env, "alex")
        req = (await trial_c.post("/requests", json={"trial_id": BRCA1_TRIAL})).json()
        r = await w.post("/present", json={"request_id": req["request_id"], "approve": True},
                         headers={"Origin": "http://localhost:8300"})
        st = (await trial_c.get(f"/requests/{req['request_id']}")).json()
        return r, st
    r, st = asyncio.run(go())
    assert r.status_code == 403 and st["status"] == "pending"


def test_trial_placeholder_page_shows_banner(env):
    r = tc(env).get("/")
    assert r.status_code == 200
    assert "Demo with synthetic data. Not medical advice." in r.text
