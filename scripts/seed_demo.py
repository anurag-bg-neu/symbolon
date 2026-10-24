"""Issue credentials for the synthetic demo people and write their wallet files.

Writes var/wallets/<person_id>.json and var/accounts.json (demo sign-in). In-process,
no HTTP. All people, genes and findings are synthetic, from data/lab_records.json.

Run from the repo root:
  macOS/Linux: .venv/bin/python scripts/seed_demo.py [--empty alex] [--empty jordan]
  Windows:     .venv\\Scripts\\python.exe scripts\\seed_demo.py [--empty alex]

--empty <person> (repeatable) starts that wallet with no results, for recording the
"Connect your lab" onboarding.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import crypto  # noqa: E402
from core.issuance import issue  # noqa: E402
from core.models import CLAIM_FINDING  # noqa: E402
from lab import auth as lab_auth  # noqa: E402
from lab import keys  # noqa: E402
from lab.service import salt_flag  # noqa: E402
from wallet.auth import make_account  # noqa: E402

RECORDS_FILE = ROOT / "data" / "lab_records.json"


def load_records() -> dict[str, dict]:
    data = json.loads(RECORDS_FILE.read_text("utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


RECORDS = load_records()
# person_id -> (carrier genes, trial findings). Kept for existing callers.
PEOPLE = {pid: (r["genes"], r["findings"]) for pid, r in RECORDS.items()}
# COUPLE -> (partner A on :8101, partner B on :8102)
COUPLES = {1: ("alex", "sam"), 2: ("jordan", "riley"), 3: ("casey", "morgan")}
PORTS = {pid: 8101 + i for pair in COUPLES.values() for i, pid in enumerate(pair)}


def write_wallet(var: Path, person_id: str, genes: list[str], findings: list[str],
                 k_lab, lab_seed: bytes, panel) -> Path:
    """Generate a holder key, issue, and write one wallet file (owner-only)."""
    holder_seed = secrets.token_bytes(32)
    cred, claims = issue(k_lab, lab_seed, person_id, genes,
                         [{"gene": g, "finding": CLAIM_FINDING} for g in findings],
                         crypto.pubkey_from_seed(holder_seed), panel,
                         salt_commitment=salt_flag())
    path = var / "wallets" / f"{person_id}.json"
    keys.write_private(path, {
        "person_id": person_id,
        "display_name": RECORDS.get(person_id, {}).get("display_name", person_id.capitalize()),
        "synthetic": True,
        "genes": sorted(genes),
        "credential": cred.model_dump(),
        "claims": [c.model_dump() for c in claims],
        "holder_seed": holder_seed.hex(),
        "lab_pubkey": crypto.pubkey_from_seed(lab_seed).hex(),
    })
    return path


def write_empty_wallet(var: Path, person_id: str, lab_seed: bytes) -> Path:
    """A wallet with no results yet. The lab key is pinned here, never from uploads."""
    path = var / "wallets" / f"{person_id}.json"
    keys.write_private(path, {
        "person_id": person_id,
        "display_name": RECORDS.get(person_id, {}).get("display_name", person_id.capitalize()),
        "synthetic": True,
        "genes": [],
        "credential": None,
        "claims": [],
        "holder_seed": secrets.token_bytes(32).hex(),
        "lab_pubkey": crypto.pubkey_from_seed(lab_seed).hex(),
    })
    return path


def write_accounts(var: Path) -> Path:
    path = var / "accounts.json"
    keys.write_private(path, {pid: make_account(pid, r["display_name"], PORTS.get(pid))
                              for pid, r in RECORDS.items()})
    return path


def seed(var: Path | None = None, empty: list[str] | tuple[str, ...] = ()) -> list[Path]:
    var = Path(var) if var else keys.var_dir()
    unknown = set(empty) - set(RECORDS)
    if unknown:
        raise ValueError(f"unknown person for --empty: {', '.join(sorted(unknown))}")
    k_lab, lab_seed = keys.load_or_create(var / "lab_keys.json")
    panel = keys.load_panel()
    paths = []
    for pid, (genes, findings) in PEOPLE.items():
        if pid in empty:
            paths.append(write_empty_wallet(var, pid, lab_seed))
        else:
            paths.append(write_wallet(var, pid, genes, findings, k_lab, lab_seed, panel))
    write_accounts(var)
    keys.write_private(var / "lab_accounts.json", lab_auth.make_accounts(RECORDS))
    (var / "lab_records.json").unlink(missing_ok=True)  # reseed resets staff edits
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed synthetic Symbolon demo wallets.")
    ap.add_argument("--empty", action="append", default=[], metavar="PERSON",
                    choices=sorted(RECORDS), help="start this wallet with no results")
    args = ap.parse_args(argv)
    for p in seed(empty=args.empty):
        print(f"wrote {p}" + (" (empty)" if p.stem in args.empty else ""))
    print("Wallet sign-in: <person>@genematch.demo / demo-<person> (synthetic data)")
    print("Lab sign-in:    <person>@demolab.test / lab-<person>, "
          f"staff {lab_auth.STAFF_EMAIL} / {lab_auth.STAFF_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
