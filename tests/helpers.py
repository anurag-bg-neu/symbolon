"""Shared test fixtures: a fixture lab, a fixture panel, and an honest relay."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field

from core import crypto
from core.issuance import issue
from core.models import CLAIM_FINDING, Credential, SignedClaim
from core.protocol import Session

DEMO_GENES = ["ASPA", "CFTR", "FANCC", "GBA", "HBB", "HEXA", "PAH", "SMN1"]


def _entry(gene: str, inheritance: str = "AR") -> dict:
    return {
        "gene": gene,
        "condition": f"{gene} condition (fixture)",
        "inheritance": inheritance,
        "summary": "fixture entry",
        "medlineplus": "https://medlineplus.gov/genetics/condition/fixture/",
    }


# Raw dicts so a non-AR entry can exist for negative tests.
PANEL = [_entry(g) for g in DEMO_GENES] + [_entry("DMD", "XL")]
# Synthetic AR genes for the MAX_SET and timing tests.
BIG_PANEL = PANEL + [_entry(f"SYN{i:02d}") for i in range(1, 31)]

COUPLES = [
    # (name_a, genes_a, name_b, genes_b, expected shared)
    ("alex", ["CFTR", "GBA", "PAH"], "sam", ["CFTR", "HEXA"], ["CFTR"]),
    ("jordan", ["HBB", "SMN1"], "riley", ["ASPA", "FANCC", "PAH"], []),
    ("casey", ["HBB", "HEXA", "SMN1"], "morgan", ["HBB", "SMN1", "GBA"], ["HBB", "SMN1"]),
]


@dataclass
class Lab:
    k_lab: bytearray = field(default_factory=crypto.random_scalar)
    seed: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    @property
    def pubkey(self) -> bytes:
        return crypto.pubkey_from_seed(self.seed)


@dataclass
class Person:
    person_id: str
    genes: list[str]
    credential: Credential
    claims: list[SignedClaim]
    holder_seed: bytes


def make_person(
    lab: Lab,
    person_id: str,
    genes: list[str],
    *,
    salt: bool = True,
    findings: list[str] = (),
    panel=None,
) -> Person:
    holder_seed = secrets.token_bytes(32)
    cred, claims = issue(
        lab.k_lab,
        lab.seed,
        person_id,
        genes,
        [{"gene": g, "finding": CLAIM_FINDING} for g in findings],
        crypto.pubkey_from_seed(holder_seed),
        panel or BIG_PANEL,
        salt_commitment=salt,
    )
    return Person(person_id, sorted(genes), cred, claims, holder_seed)


def new_session_id() -> str:
    return uuid.UUID(bytes=secrets.token_bytes(16)).hex


def make_pair(lab: Lab, a: Person, b: Person, mode: str, session_id: str | None = None):
    sid = session_id or new_session_id()
    sa = Session("initiator", mode, sid, a.credential, a.genes, lab.pubkey)
    sb = Session("joiner", mode, sid, b.credential, b.genes, lab.pubkey)
    return sa, sb


def run_pair(lab: Lab, a: Person, b: Person, mode: str):
    """Honest relay: full protocol, returns (result_a, result_b, session_a, session_b)."""
    sa, sb = make_pair(lab, a, b, mode)
    ca, cb = sa.round0(), sb.round0()
    sa.receive_commitment(cb)
    sb.receive_commitment(ca)
    xa, xb = sa.round1(), sb.round1()
    yb = sa.round2(xb)  # a · X_B, sent to Sam
    ya = sb.round2(xa)  # b · X_A, sent to Alex
    return sa.result(ya), sb.result(yb), sa, sb


def signed_in_client(app, base_url: str):
    """An in-process client for a wallet app, already signed in as its owner.

    Creates a real session in the wallet's own store and sends it as the wallet's
    cookie, exactly as the browser would after /auth/signin.
    """
    import httpx
    token = app.state.sessions.create()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url,
                             cookies={app.state.cookie_name: token})
