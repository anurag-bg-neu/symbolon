"""Static UI checks (tasks 5.2, 5.3, 7.7) plus the optional-counselor contract.

These don't drive a browser. They check the rules that are easy to break: the
banner on every page, no HTML injection sinks, calm colours, the counselor being
removable, and that both services serve their pages.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.helpers import signed_in_client

ROOT = Path(__file__).resolve().parents[1]
BANNER = "Demo with synthetic data. Not medical advice."
APP_HTML = ROOT / "wallet" / "pages" / "app.html"
HTML = [ROOT / "wallet" / "static" / "index.html", ROOT / "wallet" / "static" / "signin.html",
        APP_HTML, ROOT / "trial" / "static" / "index.html"]
# Words that must not appear on default (non-"Privacy details") screens.
JARGON = ["PSI", "ristretto", "commitment", "scalar", "nonce", "token", "blinded", "Diffie"]
JS = sorted((ROOT / "wallet" / "static").glob("*.js")) + sorted((ROOT / "trial" / "static").glob("*.js"))
CSS = [ROOT / "wallet" / "static" / "styles.css", ROOT / "trial" / "static" / "styles.css"]

_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)


@pytest.mark.parametrize("page", HTML, ids=lambda p: f"{p.parent.parent.name}-{p.name}")
def test_every_page_shows_the_banner(page):
    assert BANNER in page.read_text("utf-8")


@pytest.mark.parametrize("script", JS, ids=lambda p: p.name)
def test_no_html_injection_sinks(script):
    src = script.read_text("utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function"):
        assert sink not in src, f"{script.name} uses {sink}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
@pytest.mark.parametrize("script", JS, ids=lambda p: p.name)
def test_javascript_parses(script):
    r = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("css", CSS, ids=lambda p: p.parent.parent.name)
def test_calm_palette(css):
    src = css.read_text("utf-8").lower()
    assert not re.search(r"\bred\b|crimson|#f00\b|#ff0000", src)


def test_counselor_is_optional_in_the_ui():
    app_js = (ROOT / "wallet" / "static" / "app.js").read_text("utf-8")
    html = APP_HTML.read_text("utf-8")
    # The only use of the counselor module is behind an existence check.
    uses = [m.start() for m in re.finditer(r"GeneMatchCounselor", app_js)]
    assert uses, "app.js should render the counselor when present"
    assert "window.GeneMatchCounselor && st.explanation" in app_js
    # One removable script tag, loaded before app.js.
    assert html.count('src="/counselor.js"') == 1
    assert html.index("counselor.js") < html.index("app.js")
    # Safety text lives in app.js, not in the counselor module.
    assert "25% (1 in 4)" in app_js and "not zero risk" in app_js
    assert "genetic counselor" in app_js


@pytest.mark.parametrize("page", ["wallet/static/app.js", "wallet/static/counselor.js",
                                  "wallet/static/index.html", "wallet/static/signin.html",
                                  "wallet/static/signin.js", "wallet/pages/app.html"])
def test_default_screens_have_no_jargon(page):
    """Req 10.1: technical terms appear only in privacy.js ("Privacy details")."""
    src = (ROOT / page).read_text("utf-8")
    for word in JARGON:
        assert not re.search(rf"\b{word}\b", src, re.IGNORECASE), f"{page} says {word}"


def test_plain_language_steps_and_privacy_details_collapsed():
    app_js = (ROOT / "wallet" / "static" / "app.js").read_text("utf-8")
    privacy = (ROOT / "wallet" / "static" / "privacy.js").read_text("utf-8")
    for step in ("Sealing your list", "Exchanging scrambled lists", "Comparing"):
        assert step in app_js
    assert "Checking privately…" in app_js and '"Done."' in app_js
    assert "Privacy details" in privacy and 'el("details", "tech")' in privacy
    assert "Show technical details" in privacy
    assert ".open = true" not in privacy and "open: true" not in privacy  # closed by default
    assert "What did everyone see?" in privacy
    assert "window.GeneMatchPrivacy" in app_js


def test_privacy_summary_lines_are_plain_and_honest():
    privacy = (ROOT / "wallet" / "static" / "privacy.js").read_text("utf-8")
    for line in ('"Your partner saw: " + values + " and your lab-signed count"',
                 '"The server saw: scrambled values and your wallet\'s ID, never a gene name"',
                 '"Your genes never left this device"'):
        assert line in privacy
    # The three summary lines (outside the technical toggle) use no jargon.
    summary = privacy[privacy.index("function summaryLines"):privacy.index("function render")]
    for word in JARGON:
        assert not re.search(rf"\b{word}\b", summary, re.IGNORECASE), word


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_privacy_summary_counts_what_was_sent():
    """Run summaryLines in Node against a /check/seen-shaped object."""
    script = r"""
      global.window = {}; global.document = {};
      require(process.argv[1]);
      const s = window.GeneMatchPrivacy.summaryLines;
      console.log(JSON.stringify([s(3), s(1), s(null)]));
    """
    r = subprocess.run(["node", "-e", script, str(ROOT / "wallet" / "static" / "privacy.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    three, one, unknown = json.loads(r.stdout)
    assert three[0] == "Your partner saw: 3 scrambled values and your lab-signed count"
    assert one[0] == "Your partner saw: 1 scrambled value and your lab-signed count"
    assert unknown[0] == "Your partner saw: scrambled values and your lab-signed count"
    assert three[1:] == ["The server saw: scrambled values and your wallet's ID, never a gene name",
                         "Your genes never left this device"]


def test_invite_messages_are_one_line():
    app_js = (ROOT / "wallet" / "static" / "app.js").read_text("utf-8")
    assert '"Send this code to your partner: "' in app_js
    assert '" invited you to a private check ("' in app_js
    assert "You'll also see each other's total carrier count. Accept?" in app_js
    assert '"What does this mean?"' in app_js and 'el("details", { class: "explain" }' in app_js
    # The inviter name is set as text, never parsed as HTML.
    assert 'el("p", { class: "one-line lead", text: line })' in app_js


def test_landing_page_content():
    page = (ROOT / "wallet" / "static" / "index.html").read_text("utf-8")
    assert "Know your genetic risks." in page and "Keep your genetic secrets." in page
    assert 'href="/signin"' in page and 'href="#how"' in page
    summaries = re.findall(r"<summary>(.*?)</summary>", page)
    assert summaries == ["What is a carrier?", "Why does it matter for couples?",
                         "How does Symbolon keep it private?", "What can I do with it?",
                         "What Symbolon doesn't do"]
    assert "25% (1 in 4)" in page and "Options exist" in page
    assert "not zero risk" in page and "whether to marry or have children" in page
    assert page.count('class="walk-step"') == 4 and "Alex" in page and "Sam" in page
    # The landing page works without JavaScript: only the tiny deep-link helper loads.
    assert re.findall(r'<script src="([^"]+)"', page) == ["landing.js"]


def test_result_screen_states_the_mode_and_trial_names_requester():
    app_js = (ROOT / "wallet" / "static" / "app.js").read_text("utf-8")
    assert "Count mode: you both agreed to learn only the number" in app_js
    assert "is asking to verify your" in app_js
    assert "What won't be shared" in app_js


# --- served pages and the backend without the counselor ------------------------


@pytest.fixture
def var(tmp_path, monkeypatch):
    from relay import service as relay
    monkeypatch.setenv("GENEMATCH_VAR", str(tmp_path))
    monkeypatch.setenv("GENEMATCH_POLL", "0.005")
    relay.SESSIONS.clear()
    relay.CODES.clear()
    seed_demo.seed(tmp_path)
    return tmp_path


def test_wallet_serves_ui(var):
    from wallet.service import create_app
    c = TestClient(create_app("alex"))
    for path in ("/", "/signin", "/app.js", "/counselor.js", "/privacy.js", "/styles.css",
                 "/signin.js", "/landing.js"):
        assert c.get(path).status_code == 200, path
    assert BANNER in c.get("/").text
    # The app page needs a session; it is not reachable through the static mount.
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/signin"
    assert c.get("/app.html").status_code == 404
    c.post("/auth/demo")
    assert BANNER in c.get("/app").text
    assert c.get("/me").json()["person_id"] == "alex"  # API routes still win


def test_trial_serves_ui(var):
    from trial import service as trial_service
    c = TestClient(trial_service.create_app(lab_pubkey="00" * 32))
    assert BANNER in c.get("/").text
    assert c.get("/verifier.js").status_code == 200
    assert c.get("/trials").status_code == 200


def test_check_completes_without_the_counselor(var, monkeypatch):
    """Removing agent/ must not break the check: explanation is just null."""
    from relay import service as relay
    from wallet.service import create_app
    monkeypatch.setitem(sys.modules, "agent.counselor", None)  # import now fails

    async def go():
        rc = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app), base_url="http://relay")
        wa = signed_in_client(create_app("alex", rc), "http://a")
        wb = signed_in_client(create_app("sam", rc), "http://b")
        s = (await wa.post("/check/start", json={"mode": "detail"})).json()
        await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        await wb.post("/check/accept")
        for _ in range(500):
            st = (await wa.get("/check/status")).json()
            if st["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.01)
        seen = (await wa.get("/check/seen")).json()
        return st, seen
    st, seen = asyncio.run(go())
    assert st["state"] == "done" and st["result"]["shared_genes"] == ["CFTR"]
    assert st["explanation"] is None
    agent = next(p for p in seen["parties"] if p["party"] == "agent")
    assert agent["items"] == []


def test_check_includes_template_explanation(var):
    from relay import service as relay
    from wallet.service import create_app

    async def go():
        rc = httpx.AsyncClient(transport=httpx.ASGITransport(app=relay.app), base_url="http://relay")
        wa = signed_in_client(create_app("casey", rc), "http://a")
        wb = signed_in_client(create_app("morgan", rc), "http://b")
        s = (await wa.post("/check/start", json={"mode": "count"})).json()
        await wb.post("/check/join", json={"invite_code": s["invite_code"]})
        await wb.post("/check/accept")
        for _ in range(500):
            st = (await wa.get("/check/status")).json()
            if st["state"] in ("done", "failed"):
                return st, (await wa.get("/check/seen")).json()
            await asyncio.sleep(0.01)
    st, seen = asyncio.run(go())
    assert st["explanation"]["source"] == "template"
    assert "25%" in st["explanation"]["what_it_means"]
    blob = json.dumps([st, seen])
    panel = json.loads((ROOT / "data" / "panel.json").read_text("utf-8"))
    assert not [e["gene"] for e in panel if e["gene"] in blob]
    assert not [e["condition"] for e in panel if e["condition"] in blob]
