"""Run the Symbolon protocol against dummy, synthetic genetic data.

Creates a fake lab, a small autosomal-recessive carrier-screening panel, and two
people with lab-signed credentials, then runs the full two-party DH-PSI protocol
end-to-end in both Count and Detail modes.

No real genetic data is used. Everything here is synthetic.

Run from the repo root:
    macOS/Linux: .venv/bin/python scripts/demo_dummy_data.py
    Windows:     .venv\\Scripts\\python.exe scripts\\demo_dummy_data.py
"""

from __future__ import annotations

import secrets
import sys

# Windows consoles default to cp1252; force UTF-8 so the dashes print everywhere.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from core import crypto
from core.issuance import issue
from core.models import Credential, SignedClaim
from core.protocol import Session

# --- A dummy autosomal-recessive (AR) carrier-screening panel ----------------
# gene -> (condition, inheritance). Only AR genes are eligible for the protocol.
PANEL = [
    {"gene": "CFTR", "condition": "Cystic fibrosis", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "HBB", "condition": "Beta thalassemia / sickle cell", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "HEXA", "condition": "Tay-Sachs disease", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "SMN1", "condition": "Spinal muscular atrophy", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "GBA", "condition": "Gaucher disease", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "PAH", "condition": "Phenylketonuria", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "ASPA", "condition": "Canavan disease", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
    {"gene": "FANCC", "condition": "Fanconi anemia C", "inheritance": "AR",
     "summary": "synthetic panel entry", "medlineplus": "https://medlineplus.gov/"},
]


class DummyLab:
    """A fake lab with a token key (k_lab) and an Ed25519 signing seed."""

    def __init__(self) -> None:
        self.k_lab = crypto.random_scalar()
        self.seed = secrets.token_bytes(32)

    @property
    def pubkey(self) -> bytes:
        return crypto.pubkey_from_seed(self.seed)


def make_person(lab: DummyLab, person_id: str, genes: list[str]) -> Credential:
    """Issue a lab-signed credential for a person's carrier gene set."""
    holder_seed = secrets.token_bytes(32)
    cred, _claims = issue(
        lab.k_lab,
        lab.seed,
        person_id,
        genes,
        [],  # no detailed BRCA findings needed for the PSI demo
        crypto.pubkey_from_seed(holder_seed),
        PANEL,
    )
    return cred


def run_check(lab: DummyLab, name_a: str, cred_a: Credential, genes_a: list[str],
              name_b: str, cred_b: Credential, genes_b: list[str], mode: str):
    """Drive both wallets through the protocol via an honest relay."""
    sid = secrets.token_hex(16)
    sa = Session("initiator", mode, sid, cred_a, genes_a, lab.pubkey)
    sb = Session("joiner", mode, sid, cred_b, genes_b, lab.pubkey)

    # Round 0: commit
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb)
    sb.receive_commitment(ca)

    # Round 1: reveal blinded sets
    xa, xb = sa.round1(), sb.round1()

    # Round 2: double-blind the peer's set
    yb = sa.round2(xb)
    ya = sb.round2(xa)

    # Result: local intersection
    res_a = sa.result(ya)
    res_b = sb.result(yb)
    return res_a, res_b


def print_result(name_a, name_b, mode, res, expected):
    print(f"  mode={mode:<6}  shared_count={res.shared_count}", end="")
    if mode == "detail":
        print(f"  shared_genes={res.shared_genes}")
    else:
        print("  (genes hidden in count mode)")
    ok = res.shared_count == len(expected)
    if mode == "detail":
        ok = ok and res.shared_genes == sorted(expected)
    print(f"  expected shared: {sorted(expected)}  ->  {'OK' if ok else 'MISMATCH'}")
    return ok


def main() -> int:
    print("=" * 68)
    print("Symbolon demo  —  synthetic carrier-screening data")
    print("=" * 68)

    lab = DummyLab()
    print(f"\nDummy lab created. Public key: {lab.pubkey.hex()[:16]}...\n")

    # --- Dummy couples: (name, carrier genes) and the genes they truly share --
    couples = [
        ("Alex", ["CFTR", "GBA", "PAH"], "Sam", ["CFTR", "HEXA"], ["CFTR"]),
        ("Jordan", ["HBB", "SMN1"], "Riley", ["ASPA", "FANCC", "PAH"], []),
        ("Casey", ["HBB", "HEXA", "SMN1"], "Morgan", ["HBB", "SMN1", "GBA"],
         ["HBB", "SMN1"]),
    ]

    all_ok = True
    for name_a, genes_a, name_b, genes_b, expected in couples:
        genes_a = sorted(genes_a)
        genes_b = sorted(genes_b)
        cred_a = make_person(lab, name_a.lower(), genes_a)
        cred_b = make_person(lab, name_b.lower(), genes_b)

        print("-" * 68)
        print(f"{name_a} carries: {genes_a}")
        print(f"{name_b} carries: {genes_b}")

        for mode in ("count", "detail"):
            res_a, res_b = run_check(
                lab, name_a, cred_a, genes_a, name_b, cred_b, genes_b, mode
            )
            # Both wallets must independently agree.
            agree = res_a.shared_count == res_b.shared_count
            ok = print_result(name_a, name_b, mode, res_a, expected) and agree
            if not agree:
                print("  !! wallets disagree")
            all_ok = all_ok and ok
        print()

    print("=" * 68)
    print("RESULT:", "all checks passed" if all_ok else "SOME CHECKS FAILED")
    print("=" * 68)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
