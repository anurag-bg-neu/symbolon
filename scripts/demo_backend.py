"""Show the P2 backend working end to end, in one process, with readable output.

Runs the relay and two wallets in-process (httpx.ASGITransport), seeds a temp
var/ directory, runs all three demo couples in both modes plus failure cases.
Synthetic data only. Exit code 1 on any mismatch.
Run from the repo root: .venv/bin/python scripts/demo_backend.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["GENEMATCH_VAR"] = tempfile.mkdtemp(prefix="genematch-demo-")
os.environ.setdefault("GENEMATCH_POLL", "0.01")

import httpx  # noqa: E402

import seed_demo  # noqa: E402
from lab import keys  # noqa: E402
from relay import service as relay  # noqa: E402
from wallet.service import create_app  # noqa: E402

EXPECTED = {1: ["CFTR"], 2: [], 3: ["HBB", "SMN1"]}
GENES = {e["gene"] for e in keys.load_panel()}
failures: list[str] = []


def check(label: str, ok: bool, text: str) -> None:
    print(f"  {label:<19}-> {text:<46}{'OK' if ok else 'MISMATCH'}")
    if not ok:
        failures.append(label)


def client(app, name: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{name}")


RC = client(relay.app, "relay")


async def status(w, timeout: float = 10.0) -> dict:
    for _ in range(int(timeout / 0.01)):
        st = (await w.get("/check/status")).json()
        if st["state"] in ("done", "failed", "declined"):
            return st
        await asyncio.sleep(0.01)
    return st


async def start_join(a: str, b: str, mode: str):
    wa, wb = client(create_app(a, RC), a), client(create_app(b, RC), b)
    s = (await wa.post("/check/start", json={"mode": mode})).json()
    j = await wb.post("/check/join", json={"invite_code": s["invite_code"]})
    return wa, wb, s, j


async def full(a: str, b: str, mode: str):
    wa, wb, s, _ = await start_join(a, b, mode)
    await wb.post("/check/accept")
    sa, sb = await asyncio.gather(status(wa), status(wb))
    return sa, sb, wa, s["session_id"]


def shown(st: dict, mode: str):
    r = st["result"] or {}
    return r.get("shared_genes") if mode == "detail" else r.get("shared_count")


async def main() -> int:
    seed_demo.seed()
    pub = json.loads((keys.var_dir() / "wallets" / "alex.json").read_text())["lab_pubkey"]
    print("GeneMatch backend demo (synthetic data, not medical advice)")
    print(f"Lab public key: {pub[:16]}...\n")

    for n, (a, b) in seed_demo.COUPLES.items():
        print(f"── Couple {n}: {a} ↔ {b} ──")
        for mode in ("count", "detail"):
            sa, sb, wa, sid = await full(a, b, mode)
            want = EXPECTED[n] if mode == "detail" else len(EXPECTED[n])
            ok = (sa["state"] == sb["state"] == "done" and sa["result"] == sb["result"]
                  and shown(sa, mode) == want)
            check(mode, ok, f"{a}={shown(sa, mode)}  {b}={shown(sb, mode)}")
            if mode == "count":
                log = relay.SESSIONS[sid]["log"]
                seen = (await wa.get("/check/seen")).json()
                blob = json.dumps([log, seen, sa, sb])
                leaked = [g for g in GENES if g in blob]
                check("count-mode leaks", not leaked, f"{len(leaked)} gene names in output")
        prefixes = sum(len(e["prefixes"]) for e in relay.SESSIONS[sid]["log"])
        print(f"  relay saw {len(relay.SESSIONS[sid]['log'])} messages, "
              f"its log holds only {prefixes} 8-hex prefixes\n")

    print("── Failure cases ──")
    wa, wb, s, _ = await start_join("alex", "sam", "detail")
    await wb.post("/check/decline")
    sa = await status(wa)
    code = (sa["error"] or {}).get("code")
    check("decline", code == "PEER_DECLINED" and not relay.SESSIONS[s["session_id"]]["log"],
          f"{code}, nothing exchanged")

    wc = client(create_app("jordan", RC), "jordan")
    r = await wc.post("/check/join", json={"invite_code": s["invite_code"]})
    code = r.json().get("error", {}).get("code")
    check("reused invite code", code == "INVALID_INVITE", str(code))

    path = keys.var_dir() / "wallets" / "sam.json"
    w = json.loads(path.read_text())
    sig = w["credential"]["lab_sig"]
    w["credential"]["lab_sig"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    path.write_text(json.dumps(w))
    sa, sb, _, sid = await full("alex", "sam", "detail")
    code = (sb["error"] or {}).get("code")
    sent = [e for e in relay.SESSIONS[sid]["log"] if e["role"] == "joiner"]
    check("tampered wallet", code == "BAD_CREDENTIAL" and not sent,
          f"{code}, {len(sent)} messages sent")

    print("\nRESULT:", "all checks passed" if not failures else f"FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
