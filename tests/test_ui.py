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

ROOT = Path(__file__).resolve().parents[1]
BANNER = "Demo with synthetic data. Not medical advice."
HTML = [ROOT / "wallet" / "static" / "index.html", ROOT / "trial" / "static" / "index.html"]
JS = sorted((ROOT / "wallet" / "static").glob("*.js")) + sorted((ROOT / "trial" / "static").glob("*.js"))
CSS = [ROOT / "wallet" / "static" / "styles.css", ROOT / "trial" / "static" / "styles.css"]

_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
seed_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_demo)


@pytest.mark.parametrize("page", HTML, ids=lambda p: p.parent.parent.name)
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
    html = (ROOT / "wallet" / "static" / "index.html").read_text("utf-8")
    # The only use of the counselor module is behind an existence check.
    uses = [m.start() for m in re.finditer(r"GeneMatchCounselor", app_js)]
    assert uses, "app.js should render the counselor when present"
    assert "window.GeneMatchCounselor && st.explanation" in app_js
    # One removable script tag, loaded before app.js.
    assert html.count('src="counselor.js"') == 1
    assert html.index("counselor.js") < html.index("app.js")
    # Safety text lives in app.js, not in the counselor module.
    assert "25% (1 in 4)" in app_js and "not zero risk" in app_js
    assert "genetic counselor" in app_js


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
    for path in ("/", "/app.js", "/counselor.js", "/styles.css"):
        assert c.get(path).status_code == 200, path
    assert BANNER in c.get("/").text
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
        wa = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app("alex", rc)), base_url="http://a")
        wb = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app("sam", rc)), base_url="http://b")
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
        wa = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app("casey", rc)), base_url="http://a")
        wb = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app("morgan", rc)), base_url="http://b")
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
