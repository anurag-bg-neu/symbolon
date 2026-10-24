"""Issue credentials for the demo people and write var/wallets/<person_id>.json.

In-process, no HTTP. Synthetic data only.
Run from the repo root:
  macOS/Linux: .venv/bin/python scripts/seed_demo.py
  Windows:     .venv\\Scripts\\python.exe scripts\\seed_demo.py
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import crypto  # noqa: E402
from core.issuance import issue  # noqa: E402
from core.models import CLAIM_FINDING  # noqa: E402
from lab import keys  # noqa: E402
from lab.service import salt_flag  # noqa: E402

# person_id -> (carrier genes, trial findings)
PEOPLE = {
    "alex": (["CFTR", "GBA", "PAH"], ["BRCA1"]),
    "sam": (["CFTR", "HEXA"], []),
    "jordan": (["HBB", "SMN1"], []),
    "riley": (["ASPA", "FANCC", "PAH"], []),
    "casey": (["HBB", "HEXA", "SMN1"], []),
    "morgan": (["HBB", "SMN1", "GBA"], ["BRCA2"]),
}
# COUPLE -> (partner A on :8101, partner B on :8102)
COUPLES = {1: ("alex", "sam"), 2: ("jordan", "riley"), 3: ("casey", "morgan")}


def write_wallet(var: Path, person_id: str, genes: list[str], findings: list[str],
                 k_lab, lab_seed: bytes, panel) -> Path:
    """Generate a holder key, issue, and write one wallet file (chmod 600)."""
    holder_seed = secrets.token_bytes(32)
    cred, claims = issue(k_lab, lab_seed, person_id, genes,
                         [{"gene": g, "finding": CLAIM_FINDING} for g in findings],
                         crypto.pubkey_from_seed(holder_seed), panel,
                         salt_commitment=salt_flag())
    path = var / "wallets" / f"{person_id}.json"
    keys.write_private(path, {
        "person_id": person_id,
        "genes": sorted(genes),
        "credential": cred.model_dump(),
        "claims": [c.model_dump() for c in claims],
        "holder_seed": holder_seed.hex(),
        "lab_pubkey": crypto.pubkey_from_seed(lab_seed).hex(),
    })
    return path


def seed(var: Path | None = None) -> list[Path]:
    var = Path(var) if var else keys.var_dir()
    k_lab, lab_seed = keys.load_or_create(var / "lab_keys.json")
    panel = keys.load_panel()
    return [write_wallet(var, pid, genes, findings, k_lab, lab_seed, panel)
            for pid, (genes, findings) in PEOPLE.items()]


if __name__ == "__main__":
    for p in seed():
        print(f"wrote {p}")
