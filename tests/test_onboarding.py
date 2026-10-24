"""Tasks 10.2, 10.5, 10.6: seeding, Connect your lab, lab files, and uploads."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

from core import crypto
from core.issuance import issue
from core.labfile import make_lab_file, verify_lab_file
from core.protocol import Session
from lab import keys
from lab import service as lab_service
from relay import service as relay
from tests.helpers import Lab, signed_in_client
from wallet.service import create_app

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)


@pytest.fixture
def var(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setenv("GENEMATCH_POLL", "0.005")
    monkeypatch.setattr(lab_service, "ALLOWED_RETURN_HOSTS",
                        {"localhost", "127.0.0.1", "alex", "sam", "jordan"})
    relay.SESSIONS.clear()
    relay.CODES.clear()
    lab_service.CONNECT.clear()
    lab_service.SESSIONS.clear()
    seed_demo.seed(tmp_path)
    return tmp_path


def lab_tc(person: str | None = None) -> TestClient:
    """A browser-like lab client, optionally signed in (person or "staff")."""
    c = TestClient(lab_service.app)
    if person:
        email = "staff@lab.genematch.demo" if person == "staff" else f"{person}@lab.genematch.demo"
        pw = "lab-staff" if person == "staff" else f"lab-{person}"
        r = c.post("/signin", content=urlencode({"email": email, "password": pw}), headers=FORM,
                   follow_redirects=False)
        assert r.status_code == 303, r.text
    return c


def new_request(c: TestClient, person="alex") -> dict:
    return c.post("/connect/requests", json={"person_id": person, "holder_pubkey": "11" * 32,
                                             "return_url": "http://localhost:8101/app"}).json()


def wallet_file(var, person) -> dict:
    return json.loads((var / "wallets" / f"{person}.json").read_text())


def self_check(w: dict) -> None:
    s = Session("initiator", "count", "x", w["credential"], w["genes"], w["lab_pubkey"])
    s.zero()


class Spy:
    """Records every request the wallet sends to the lab."""

    def __init__(self):
        self.bodies: list[str] = []

    async def __call__(self, request: httpx.Request) -> None:
        self.bodies.append(f"{request.url} {request.headers} {request.content.decode()}")


def clients(person, spy=None):
    lab_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=lab_service.app),
                              base_url="http://lab",
                              event_hooks={"request": [spy]} if spy else None)
    relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app), base_url="http://relay")
    app = create_app(person, relay_c, None, lab_c)
    return signed_in_client(app, f"http://{person}"), lab_c, relay_c


# --- seeding -----------------------------------------------------------------------


def test_default_seed_loads_every_wallet(var):
    for pid, (genes, _) in seed_demo.PEOPLE.items():
        w = wallet_file(var, pid)
        assert w["credential"] is not None and w["genes"] == sorted(genes)
        assert w["synthetic"] is True and w["display_name"]
        self_check(w)


def test_records_drawn_from_panel():
    panel = {e["gene"] for e in keys.load_panel()}
    for r in seed_demo.RECORDS.values():
        assert set(r["genes"]) <= panel


def test_empty_flag_leaves_only_that_wallet_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    assert seed_demo.main(["--empty", "alex"]) == 0
    alex = wallet_file(tmp_path, "alex")
    assert alex["credential"] is None and alex["genes"] == [] and alex["claims"] == []
    assert alex["lab_pubkey"] == wallet_file(tmp_path, "sam")["lab_pubkey"]
    for pid in seed_demo.PEOPLE:
        if pid != "alex":
            assert wallet_file(tmp_path, pid)["credential"] is not None
    assert (tmp_path / "accounts.json").exists()


def test_empty_flag_is_repeatable_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    seed_demo.main(["--empty", "alex", "--empty", "jordan"])
    assert wallet_file(tmp_path, "alex")["credential"] is None
    assert wallet_file(tmp_path, "jordan")["credential"] is None
    assert wallet_file(tmp_path, "sam")["credential"] is not None
    with pytest.raises(SystemExit):
        seed_demo.main(["--empty", "nobody"])


def test_empty_wallet_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    seed_demo.seed(tmp_path, empty=["alex"])
    c = TestClient(create_app("alex"))
    c.post("/auth/demo")
    me = c.get("/me").json()
    assert me["has_results"] is False and me["genes"] == [] and me["synthetic"] is True
    for path, body in (("/check/start", {"mode": "count"}), ("/check/join", {"invite_code": "X"})):
        r = c.post(path, json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "NO_RESULTS"


# --- Connect your lab ------------------------------------------------------------


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


async def lab_signin(lab_c, email: str, password: str, next_url: str = "/my-results"):
    return await lab_c.post("/signin", content=urlencode(
        {"email": email, "password": password, "next": next_url}), headers=FORM)


async def connect(w, lab_c, approve=True, person="alex"):
    r = await w.post("/onboard/connect")
    assert r.status_code == 200, r.text
    consent = r.json()["consent_url"]
    rid = consent.rsplit("/", 1)[1]
    # The patient signs in to the lab before seeing the consent page.
    gate = await lab_c.get(f"/connect/{rid}")
    assert gate.status_code == 303 and gate.headers["location"] == f"/signin?next=/connect/{rid}"
    signed = await lab_signin(lab_c, f"{person}@lab.genematch.demo", f"lab-{person}", f"/connect/{rid}")
    assert signed.status_code == 303 and signed.headers["location"] == f"/connect/{rid}"
    page = await lab_c.get(f"/connect/{rid}")
    action = "approve" if approve else "decline"
    back = await lab_c.post(f"/connect/{rid}/{action}")
    fin = await w.post("/onboard/connect/finish", json={"request_id": rid})
    return rid, page, back, fin


def test_connect_reissues_and_passes_self_check(var):
    spy = Spy()
    before = wallet_file(var, "alex")

    async def go():
        w, lab_c, _ = clients("alex", spy)
        return await connect(w, lab_c)
    rid, page, back, fin = asyncio.run(go())

    assert "GeneMatch Demo Lab wants to send your carrier results to this wallet" in page.text
    assert "Synthetic demo data" in page.text and "Demo with synthetic data" in page.text
    assert back.status_code == 303 and back.headers["location"] == f"http://alex/app#connect={rid}"
    assert fin.json() == {"status": "connected", "count": 3, "findings": 1}

    after = wallet_file(var, "alex")
    self_check(after)
    assert after["genes"] == ["CFTR", "GBA", "PAH"]
    assert after["holder_seed"] != before["holder_seed"]   # new key, made by the wallet
    pub = crypto.pubkey_from_seed(bytes.fromhex(after["holder_seed"])).hex()
    assert [c["holder_pubkey"] for c in after["claims"]] == [pub]
    # The holder seed never left the wallet; only the public key did.
    sent = " ".join(spy.bodies)
    assert after["holder_seed"] not in sent and pub in sent


def test_connect_works_for_empty_wallet(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setattr(lab_service, "ALLOWED_RETURN_HOSTS", {"sam"})
    seed_demo.seed(tmp_path, empty=["sam"])

    async def go():
        w, lab_c, _ = clients("sam")
        _, _, _, fin = await connect(w, lab_c, person="sam")
        return fin, (await w.get("/me")).json()
    fin, me = asyncio.run(go())
    assert fin.json()["status"] == "connected" and me["has_results"] is True
    assert me["genes"] == ["CFTR", "HEXA"]


def test_connect_decline_issues_nothing(var):
    before = wallet_file(var, "alex")

    async def go():
        w, lab_c, _ = clients("alex")
        return await connect(w, lab_c, approve=False)
    rid, _, back, fin = asyncio.run(go())
    assert back.headers["location"].endswith("#connect-declined")
    assert fin.json()["status"] == "declined"
    assert wallet_file(var, "alex") == before


def test_connect_lab_issues_only_its_records(var):
    entry = lab_service.issue_for("riley", crypto.pubkey_from_seed(b"\x01" * 32).hex())
    assert entry["lab_file"]["genes"] == ["ASPA", "FANCC", "PAH"]
    assert entry["claims"] == []


def test_connect_result_is_single_use_and_needs_token(var):
    c = lab_tc("alex")
    r = new_request(c)
    rid, token = r["request_id"], r["collect_token"]
    assert token not in r["consent_url"]
    assert c.get(f"/connect/{rid}/result", headers={"X-Collect-Token": token}).status_code == 202
    c.post(f"/connect/{rid}/approve", follow_redirects=False)
    assert c.get(f"/connect/{rid}/result").status_code == 404                     # no token
    assert c.get(f"/connect/{rid}/result", headers={"X-Collect-Token": "x" * 64}).status_code == 404
    ok = c.get(f"/connect/{rid}/result", headers={"X-Collect-Token": token})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert c.get(f"/connect/{rid}/result", headers={"X-Collect-Token": token}).status_code == 404
    assert c.post(f"/connect/{rid}/approve").status_code == 404


@pytest.mark.parametrize("body,code", [
    ({"person_id": "nobody", "holder_pubkey": "11" * 32,
      "return_url": "http://localhost:8101/app"}, 404),
    ({"person_id": "alex", "holder_pubkey": "zz",
      "return_url": "http://localhost:8101/app"}, 422),
    ({"person_id": "alex", "holder_pubkey": "11" * 32,
      "return_url": "https://evil.example/app"}, 422),
    ({"person_id": "alex", "holder_pubkey": "11" * 32,
      "return_url": "http://localhost:8101/elsewhere"}, 422),
    ({"person_id": "alex", "holder_pubkey": "11" * 32,
      "return_url": "javascript:alert(1)"}, 422),
])
def test_connect_request_validation(var, body, code):
    assert TestClient(lab_service.app).post("/connect/requests", json=body).status_code == code


def test_connect_expired(var, monkeypatch):
    c = TestClient(lab_service.app)
    r = c.post("/connect/requests", json={"person_id": "alex", "holder_pubkey": "11" * 32,
                                          "return_url": "http://localhost:8101/app"}).json()
    lab_service.CONNECT[r["request_id"]]["expires"] -= 3600
    assert c.get(f"/connect/{r['request_id']}").status_code == 404


def test_connect_lab_unavailable(var):
    def down(request):
        raise httpx.ConnectError("lab down")

    async def go():
        dead = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://lab")
        w = signed_in_client(create_app("alex", None, None, dead), "http://alex")
        return await w.post("/onboard/connect")
    r = asyncio.run(go())
    assert r.status_code == 502 and r.json()["error"]["code"] == "LAB_UNAVAILABLE"


def test_connect_finish_unknown_request(var):
    async def go():
        w, _, _ = clients("alex")
        return await w.post("/onboard/connect/finish", json={"request_id": "a" * 32})
    r = asyncio.run(go())
    assert r.status_code == 404 and r.json()["error"]["code"] == "CONNECT_EXPIRED"


def test_connect_consent_page_escapes(var, monkeypatch):
    monkeypatch.setattr(lab_service, "load_records", lambda: {
        "alex": {"display_name": "<script>x</script>", "genes": ["CFTR"], "findings": []}})
    c = lab_tc("alex")
    page = c.get(f"/connect/{new_request(c)['request_id']}").text
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page


# --- fix 2: only the signed-in patient can release their results -------------------


def test_consent_requires_lab_signin(var):
    c = lab_tc()
    rid = new_request(c)["request_id"]
    r = c.get(f"/connect/{rid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/signin?next=/connect/{rid}"
    assert c.post(f"/connect/{rid}/approve", follow_redirects=False).status_code == 401
    assert c.post(f"/connect/{rid}/decline", follow_redirects=False).status_code == 401
    assert lab_service.CONNECT[rid]["status"] == "pending"


@pytest.mark.parametrize("who", ["sam", "staff"])
def test_other_patient_or_staff_cannot_release_results(var, who):
    c = lab_tc(who)
    rid = new_request(c, "alex")["request_id"]
    page = c.get(f"/connect/{rid}")
    assert page.status_code == 403
    assert "This wallet belongs to Alex" in page.text and "Nothing was sent" in page.text
    assert "Send my results" not in page.text
    assert c.post(f"/connect/{rid}/approve", follow_redirects=False).status_code == 403
    assert c.post(f"/connect/{rid}/decline", follow_redirects=False).status_code == 403
    assert lab_service.CONNECT[rid]["status"] == "pending"
    assert lab_service.CONNECT[rid]["result"] is None


def test_lab_signin_wrong_password(var):
    c = TestClient(lab_service.app)
    r = c.post("/signin", content=urlencode({"email": "alex@lab.genematch.demo", "password": "x"}),
               headers=FORM)
    assert r.status_code == 401 and "don&#x27;t match" in r.text
    assert c.get("/my-results", follow_redirects=False).status_code == 303


def test_lab_signin_next_is_restricted(var):
    c = TestClient(lab_service.app)
    r = c.post("/signin", content=urlencode({"email": "alex@lab.genematch.demo",
                                             "password": "lab-alex", "next": "https://evil.example"}),
               headers=FORM, follow_redirects=False)
    assert r.headers["location"] == "/my-results"
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("gm_lab_session=") and "httponly" in cookie and "samesite=lax" in cookie


def test_lab_signout(var):
    c = lab_tc("alex")
    assert c.get("/my-results").status_code == 200
    c.post("/signout", follow_redirects=False)
    assert c.get("/my-results", follow_redirects=False).status_code == 303


def test_lab_posts_need_same_origin(var):
    c = lab_tc("alex")
    rid = new_request(c)["request_id"]
    r = c.post(f"/connect/{rid}/approve", headers={"Origin": "https://evil.example"},
               follow_redirects=False)
    assert r.status_code == 403 and lab_service.CONNECT[rid]["status"] == "pending"


def test_demo_logins_can_be_hidden(var, monkeypatch):
    assert "lab-alex" in TestClient(lab_service.app).get("/signin").text
    monkeypatch.setenv("GENEMATCH_SHOW_DEMO_LOGINS", "0")
    assert "lab-alex" not in TestClient(lab_service.app).get("/signin").text


def test_lab_accounts_are_hashed(var):
    text = (var / "lab_accounts.json").read_text("utf-8")
    assert "lab-alex" not in text and "lab-staff" not in text
    accts = json.loads(text)
    assert accts["_staff"]["role"] == "staff" and accts["alex"]["role"] == "patient"


# --- fix 1: patients can't choose genes; staff edit records --------------------------


def test_old_create_lab_file_page_is_gone(var):
    c = lab_tc("alex")
    assert c.get("/lab-files").status_code == 404
    assert c.post("/lab-files", content="person=alex&genes=HBB", headers=FORM).status_code in (404, 405)


def test_my_results_requires_signin(var):
    c = TestClient(lab_service.app)
    r = c.get("/my-results", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/signin?next=/my-results"
    assert c.get("/my-results/file", follow_redirects=False).status_code == 303


def test_download_your_results_file_is_own_record_only(var):
    c = lab_tc("riley")
    page = c.get("/my-results").text
    assert "Your results, Riley." in page and "Synthetic demo data" in page
    assert "ASPA" in page and "CFTR" not in page
    r = c.get("/my-results/file?genes=CFTR&person=alex")   # attempts to choose are ignored
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    _, genes, name = verify_lab_file(r.json(), wallet_file(var, "riley")["lab_pubkey"], "riley")
    assert genes == ["ASPA", "FANCC", "PAH"] and name == "Riley"


def test_staff_cannot_download_patient_files(var):
    assert lab_tc("staff").get("/my-results/file").status_code == 403


def test_staff_records_page_is_staff_only(var):
    assert TestClient(lab_service.app).get("/staff/records",
                                           follow_redirects=False).status_code == 303
    c = lab_tc("alex")
    assert c.get("/staff/records").status_code == 403
    assert c.post("/staff/records/alex", content="genes=HBB", headers=FORM).status_code == 403
    page = lab_tc("staff").get("/staff/records").text
    assert "Patient records" in page and all(f'value="{e["gene"]}"' in page for e in keys.load_panel())


def test_staff_edit_reaches_patient_on_next_download_and_reseed_resets(var):
    staff = lab_tc("staff")
    r = staff.post("/staff/records/alex", content="genes=HBB&genes=SMN1", headers=FORM)
    assert r.status_code == 200 and "Saved Alex" in r.text
    doc = lab_tc("alex").get("/my-results/file").json()
    assert doc["genes"] == ["HBB", "SMN1"]
    assert lab_service.issue_for("alex", "11" * 32)["lab_file"]["genes"] == ["HBB", "SMN1"]
    seed_demo.seed(var)
    assert lab_tc("alex").get("/my-results/file").json()["genes"] == ["CFTR", "GBA", "PAH"]


@pytest.mark.parametrize("genes,message", [
    (["NOTAGENE"], "gene not in panel"),
    (["CFTR", "CFTR"], "duplicate genes"),
    ([f"G{i}" for i in range(21)], "more than 20 genes"),
])
def test_staff_edit_uses_issuance_checks(var, genes, message):
    r = lab_tc("staff").post("/staff/records/alex",
                             content="&".join(f"genes={g}" for g in genes), headers=FORM)
    assert r.status_code == 400 and message in r.text
    assert not (var / "lab_records.json").exists()


def test_staff_edit_unknown_patient(var):
    assert lab_tc("staff").post("/staff/records/nobody", content="genes=HBB",
                                headers=FORM).status_code == 404


def test_lab_files_rejects_non_ar(var, monkeypatch):
    panel = keys.load_panel() + [{"gene": "DMD", "condition": "x", "inheritance": "XL",
                                  "summary": "s", "medlineplus": "u"}]
    monkeypatch.setattr(lab_service.keys, "load_panel", lambda: panel)
    with pytest.raises(lab_service.IssuanceError, match="not autosomal recessive"):
        lab_service.make_custom_lab_file("alex", ["DMD"], "Alex")


def test_make_lab_file_script(var, tmp_path):
    out = tmp_path / "alex.json"
    env = {**os.environ, "GENEMATCH_VAR": str(var)}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_lab_file.py"),
                        "--person", "alex", "--genes", "CFTR,HBB", "--out", str(out)],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    _, genes, _ = verify_lab_file(json.loads(out.read_text()),
                                  wallet_file(var, "alex")["lab_pubkey"], "alex")
    assert genes == ["CFTR", "HBB"]
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_lab_file.py"),
                          "--person", "alex", "--genes", "NOTAGENE", "--out", str(out)],
                         capture_output=True, text=True, env=env, cwd=ROOT)
    assert bad.returncode == 2 and "gene not in panel" in bad.stderr


# --- lab files: loading into the wallet --------------------------------------------


def lab_file(person, genes, name=None) -> bytes:
    return json.dumps(lab_service.make_custom_lab_file(person, genes, name or person.title())).encode()


async def upload(w, data: bytes, filename="results.json"):
    return await w.post("/onboard/upload", content=data,
                        headers={"Content-Type": "application/octet-stream", "X-File-Name": filename})


def run(coro):
    return asyncio.run(coro)


def test_valid_file_needs_confirmation_then_replaces(var):
    before = wallet_file(var, "alex")

    async def go():
        w, _, _ = clients("alex")
        prev = (await upload(w, lab_file("alex", ["HEXA", "PAH"]))).json()
        unchanged = (await w.get("/me")).json()
        conf = (await w.post("/onboard/upload/confirm", json={"pending_id": prev["pending_id"]})).json()
        again = await w.post("/onboard/upload/confirm", json={"pending_id": prev["pending_id"]})
        return prev, unchanged, conf, again, (await w.get("/me")).json()
    prev, unchanged, conf, again, me = run(go())
    assert prev["replaces"] is True and prev["current_count"] == 3
    assert prev["genes"] == ["HEXA", "PAH"] and prev["display_name"] == "Alex"
    assert unchanged["genes"] == ["CFTR", "GBA", "PAH"]   # nothing changes before confirming
    assert conf == {"status": "replaced", "count": 2}
    assert again.status_code == 404                        # single use
    assert me["genes"] == ["HEXA", "PAH"]
    after = wallet_file(var, "alex")
    self_check(after)
    assert after["claims"] == before["claims"] and after["holder_seed"] == before["holder_seed"]


def test_uploaded_file_works_in_a_full_check_against_seeded_partner(var):
    """Custom alex [HEXA, PAH] vs seeded sam [CFTR, HEXA] -> shared HEXA."""
    async def go():
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app), base_url="http://relay")
        wa = signed_in_client(create_app("alex", relay_c), "http://alex")
        wb = signed_in_client(create_app("sam", relay_c), "http://sam")
        prev = (await upload(wa, lab_file("alex", ["HEXA", "PAH"]))).json()
        await wa.post("/onboard/upload/confirm", json={"pending_id": prev["pending_id"]})
        results = {}
        for mode in ("detail", "count"):
            s = (await wa.post("/check/start", json={"mode": mode})).json()
            await wb.post("/check/join", json={"invite_code": s["invite_code"]})
            await wb.post("/check/accept")
            for _ in range(500):
                st = (await wa.get("/check/status")).json()
                if st["state"] in ("done", "failed"):
                    break
                await asyncio.sleep(0.01)
            results[mode] = st
        return results
    res = run(go())
    assert res["detail"]["state"] == "done" and res["detail"]["result"]["shared_genes"] == ["HEXA"]
    assert res["count"]["result"]["shared_count"] == 1


def _tamper_genes(data: bytes) -> bytes:
    d = json.loads(data)
    d["genes"] = ["CFTR", "HEXA"]
    return json.dumps(d).encode()


def _other_lab_file() -> bytes:
    rogue = Lab()
    cred, _ = issue(rogue.k_lab, rogue.seed, "alex", ["CFTR"], [], bytes(32), keys.load_panel())
    return json.dumps(make_lab_file(rogue.seed, cred, ["CFTR"], "Alex")).encode()


VCF = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n7\t117559590\trs113993960\tATCT\tA\n"
FASTQ = b"@read1\nACGTACGTACGT\n+\nIIIIIIIIIIII\n@read2\nACGT\n+\nIIII\n"
CONSUMER = (b"# This data file generated by a consumer DNA service\n# rsid\tchromosome\tposition\tgenotype\n"
            b"rs4477212\t1\t82154\tAA\nrs3094315\t1\t752566\tAG\nrs3131972\t1\t752721\tAG\n")


@pytest.mark.parametrize("case,expected", [
    ("tampered_genes", "TAMPERED"),
    ("wrong_signature", "NOT_SIGNED"),
    ("wrong_person", "WRONG_PERSON"),
    ("malformed_json", "NOT_LAB_FILE"),
    ("wrong_structure", "MALFORMED_FILE"),
    ("not_json", "NOT_LAB_FILE"),
    ("vcf", "RAW_DNA"),
    ("fastq", "RAW_DNA"),
    ("consumer_export", "RAW_DNA"),
    ("vcf_by_name", "RAW_DNA"),
    ("gzip", "RAW_DNA"),
])
def test_bad_uploads_are_rejected_with_the_right_message(var, case, expected):
    before = wallet_file(var, "alex")
    name = "results.json"
    if case == "tampered_genes":
        data = _tamper_genes(lab_file("alex", ["CFTR", "HBB"]))
    elif case == "wrong_signature":
        data = _other_lab_file()
    elif case == "wrong_person":
        data = lab_file("sam", ["CFTR"], "Sam")
    elif case == "malformed_json":
        data = b'{"format": "genematch-lab-file/v1", '
    elif case == "wrong_structure":
        data = json.dumps({"format": "genematch-lab-file/v1"}).encode()
    elif case == "not_json":
        data = b"hello, these are my results"
    elif case == "vcf":
        data = VCF
    elif case == "fastq":
        data = FASTQ
    elif case == "consumer_export":
        data = CONSUMER
    elif case == "vcf_by_name":
        data, name = b"anything", "me.vcf"
    else:
        data = b"\x1f\x8b\x08\x00rest"

    async def go():
        w, _, _ = clients("alex")
        return await upload(w, data, name)
    r = run(go())
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == expected
    assert "Nothing" in err["user_message"]
    if expected == "RAW_DNA":
        assert "signed by a lab" in err["user_message"] and "fake" in err["user_message"]
    if expected == "WRONG_PERSON":
        assert "This file belongs to Sam" in err["user_message"]
    if expected == "NOT_SIGNED":
        assert "isn't signed by the lab" in err["user_message"]
    assert wallet_file(var, "alex") == before


def test_oversized_upload_rejected(var):
    async def go():
        w, _, _ = clients("alex")
        return await upload(w, b"{" + b" " * (100 * 1024) + b"}")
    r = run(go())
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_upload_into_empty_wallet(tmp_path, monkeypatch):
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    seed_demo.seed(tmp_path, empty=["alex"])

    async def go():
        w, _, _ = clients("alex")
        prev = (await upload(w, lab_file("alex", ["CFTR"]))).json()
        await w.post("/onboard/upload/confirm", json={"pending_id": prev["pending_id"]})
        return prev, (await w.get("/me")).json()
    prev, me = run(go())
    assert prev["replaces"] is False and me["has_results"] is True and me["genes"] == ["CFTR"]
    self_check(wallet_file(tmp_path, "alex"))


def test_upload_never_reaches_the_relay(var):
    seen: list[str] = []

    async def hook(request):
        seen.append(str(request.url))

    async def go():
        relay_c = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app),
                                    base_url="http://relay", event_hooks={"request": [hook]})
        w = signed_in_client(create_app("alex", relay_c), "http://alex")
        prev = (await upload(w, lab_file("alex", ["CFTR"]))).json()
        await w.post("/onboard/upload/confirm", json={"pending_id": prev["pending_id"]})
    run(go())
    assert seen == []


def test_upload_requires_same_origin(var):
    async def go():
        w, _, _ = clients("alex")
        return await w.post("/onboard/upload", content=lab_file("alex", ["CFTR"]),
                            headers={"Origin": "http://localhost:8300"})
    assert run(go()).status_code == 403
