"""Task 10.1 / Requirement 12: demo sign-in, one person per wallet port."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wallet.auth import Sessions, hash_password, verify_password

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)

PROTECTED = [("GET", "/me"), ("GET", "/check/status"), ("GET", "/check/seen"),
             ("POST", "/check/start"), ("POST", "/check/accept"),
             ("GET", "/present/pending?request_id=" + "a" * 32), ("POST", "/present"),
             ("POST", "/onboard/connect"), ("POST", "/onboard/upload"),
             ("POST", "/onboard/upload/confirm")]


@pytest.fixture
def var(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    seed_demo.seed(tmp_path)
    return tmp_path


def wallet(person="alex") -> TestClient:
    from wallet.service import create_app
    return TestClient(create_app(person))


def test_correct_account_signs_in(var):
    c = wallet("alex")
    r = c.post("/auth/signin", json={"email": "alex@genematch.demo", "password": "demo-alex"})
    assert r.status_code == 200 and r.json()["next"] == "/app"
    assert c.get("/me").json()["person_id"] == "alex"
    assert c.get("/auth/session").json()["signed_in"] is True


def test_email_is_case_insensitive(var):
    c = wallet("alex")
    r = c.post("/auth/signin", json={"email": " Alex@GeneMatch.demo ", "password": "demo-alex"})
    assert r.status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_message(var):
    c = wallet("alex")
    bad = c.post("/auth/signin", json={"email": "alex@genematch.demo", "password": "nope"})
    unknown = c.post("/auth/signin", json={"email": "who@genematch.demo", "password": "nope"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    assert bad.json()["error"]["code"] == "BAD_LOGIN"
    assert c.get("/me").status_code == 401


def test_wrong_account_on_port_is_rejected_with_message(var):
    c = wallet("alex")
    r = c.post("/auth/signin", json={"email": "sam@genematch.demo", "password": "demo-sam"})
    assert r.status_code == 403
    assert r.json()["error"] == {
        "code": "WRONG_WALLET",
        "user_message": "This wallet belongs to Alex. Open Sam's wallet at localhost:8102."}
    assert c.get("/me").status_code == 401


def test_wrong_wallet_needs_the_right_password(var):
    """Don't reveal another person's wallet unless their password is correct."""
    c = wallet("alex")
    r = c.post("/auth/signin", json={"email": "sam@genematch.demo", "password": "guess"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "BAD_LOGIN"


def test_other_couple_ports(var):
    r = wallet("riley").post("/auth/signin", json={"email": "jordan@genematch.demo",
                                                    "password": "demo-jordan"})
    assert r.json()["error"]["user_message"] == (
        "This wallet belongs to Riley. Open Jordan's wallet at localhost:8101.")


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_api_requires_session(var, method, path):
    r = wallet().request(method, path, json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SIGNED_OUT"


def test_app_page_redirects_to_signin(var):
    r = wallet().get("/app", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/signin"


def test_public_pages_need_no_session(var):
    c = wallet()
    for path in ("/", "/signin", "/styles.css", "/auth/session"):
        assert c.get(path).status_code == 200, path


def test_continue_as_demo_button(var):
    c = wallet("sam")
    s = c.get("/auth/session").json()
    assert s == {"signed_in": False, "person_id": "sam", "display_name": "Sam",
                 "demo_email": "sam@genematch.demo", "demo_password": "demo-sam"}
    assert c.post("/auth/demo").status_code == 200
    assert c.get("/me").json()["person_id"] == "sam"


def test_signout_invalidates_the_session(var):
    c = wallet()
    c.post("/auth/demo")
    token = c.cookies.get("gm_session_alex")
    assert token and c.get("/me").status_code == 200
    assert c.post("/auth/signout").status_code == 200
    assert c.get("/me").status_code == 401
    # Replaying the old cookie no longer works.
    c.cookies.set("gm_session_alex", token)
    assert c.get("/me").status_code == 401


def test_cookie_flags(var):
    r = wallet().post("/auth/demo")
    header = r.headers["set-cookie"].lower()
    assert header.startswith("gm_session_alex=")
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header


def test_wallets_use_different_cookie_names(var):
    """Cookies on localhost ignore the port; per-wallet names keep them apart."""
    a, b = wallet("alex"), wallet("sam")
    a.post("/auth/demo")
    stolen = a.cookies.get("gm_session_alex")
    b.cookies.set("gm_session_alex", stolen)
    assert b.get("/me").status_code == 401  # Alex's session means nothing to Sam's wallet


def test_signin_requires_same_origin(var):
    r = wallet().post("/auth/signin", json={"email": "alex@genematch.demo", "password": "demo-alex"},
                      headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_accounts_file_has_no_plaintext_passwords(var):
    text = (var / "accounts.json").read_text("utf-8")
    accounts = json.loads(text)
    assert set(accounts) == set(seed_demo.PEOPLE)
    for pid, rec in accounts.items():
        assert f"demo-{pid}" not in text
        assert set(rec) == {"email", "display_name", "port", "salt", "hash", "n", "r", "p"}
        assert verify_password(f"demo-{pid}", rec)
    assert accounts["alex"]["port"] == 8101 and accounts["sam"]["port"] == 8102
    # No genetic data stored with accounts.
    assert not any(g in text for g in ("CFTR", "HBB", "BRCA"))


def test_password_hashing():
    rec = hash_password("secret")
    assert verify_password("secret", rec)
    assert not verify_password("Secret", rec)
    assert hash_password("secret")["hash"] != rec["hash"]  # fresh salt
    assert not verify_password("secret", {"salt": "zz"})


def test_sessions_expire():
    now = [1000.0]
    s = Sessions(ttl=10, clock=lambda: now[0])
    t = s.create()
    assert s.valid(t)
    now[0] += 11
    assert not s.valid(t) and not s.valid(None) and not s.valid("forged")
