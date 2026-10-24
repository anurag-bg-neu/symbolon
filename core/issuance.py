"""Credential and claim issuance. Called by the lab service and the seed script.

Pure: keys and the panel are passed in; nothing is read from disk or env.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from core import crypto
from core.models import (
    CLAIM_FINDING,
    CLAIM_GENES,
    MAX_SET,
    BlindedSet,
    Credential,
    PanelEntry,
    SignedClaim,
)


class IssuanceError(ValueError):
    """The lab refuses to issue. The message names the rule, never patient data."""


def format_time(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def credential_signed_fields(cred: Credential | BlindedSet) -> dict[str, Any]:
    """The exact object the lab signs (tokens are covered via the commitment)."""
    return {
        "person_id": cred.person_id,
        "count": cred.count,
        "commitment": cred.commitment,
        "issued_at": cred.issued_at,
    }


def claim_signed_fields(claim: SignedClaim) -> dict[str, Any]:
    """The claim without lab_sig."""
    return claim.model_dump(exclude={"lab_sig"})


def _panel_index(panel: Iterable[Any] | Mapping[str, Any]) -> dict[str, str]:
    """gene -> inheritance, from PanelEntry objects or raw dicts."""
    items = panel.values() if isinstance(panel, Mapping) else panel
    index: dict[str, str] = {}
    for entry in items:
        if isinstance(entry, PanelEntry):
            index[entry.gene] = entry.inheritance
        else:
            index[entry["gene"]] = entry.get("inheritance", "")
    return index


def _finding_pairs(findings: Iterable[Any]) -> list[tuple[str, str]]:
    pairs = []
    for f in findings:
        if isinstance(f, Mapping):
            pairs.append((f["gene"], f["finding"]))
        else:
            gene, finding = f
            pairs.append((gene, finding))
    return pairs


def issue(
    k_lab: bytes | bytearray,
    lab_seed: bytes,
    person_id: str,
    genes: Iterable[str],
    findings: Iterable[Any],
    holder_pubkey: bytes | str,
    panel: Iterable[Any] | Mapping[str, Any],
    *,
    salt_commitment: bool = True,
    now: datetime | None = None,
) -> tuple[Credential, list[SignedClaim]]:
    genes = list(genes)
    pairs = _finding_pairs(findings)
    index = _panel_index(panel)

    if not person_id:
        raise IssuanceError("person_id is required")
    if len(set(genes)) != len(genes):
        raise IssuanceError("duplicate genes")
    if len(genes) > MAX_SET:
        raise IssuanceError(f"more than {MAX_SET} genes")
    for g in genes:
        if g not in index:
            raise IssuanceError("gene not in panel")
        if index[g] != "AR":
            raise IssuanceError("gene is not autosomal recessive")
    claim_genes = [g for g, _ in pairs]
    if len(set(claim_genes)) != len(claim_genes):
        raise IssuanceError("duplicate findings")
    for g, finding in pairs:
        if g not in CLAIM_GENES:
            raise IssuanceError("finding gene not supported for claims")
        if finding != CLAIM_FINDING:
            raise IssuanceError("unsupported finding")

    holder_hex = (
        holder_pubkey if isinstance(holder_pubkey, str) else crypto.to_hex(holder_pubkey)
    )
    crypto.from_hex(holder_hex, 32)  # shape check
    issued_at = format_time(now)

    # tokens[i] belongs to genes[i]; both in sorted gene order.
    genes = sorted(genes)
    tokens = [crypto.mul(k_lab, crypto.hash_to_group(g)) for g in genes]
    salt = secrets.token_bytes(crypto.SALT_BYTES) if salt_commitment else None
    commitment = crypto.commit_tokens(tokens, salt)

    unsigned = Credential.model_construct(
        person_id=person_id,
        count=len(tokens),
        commitment=commitment.hex(),
        issued_at=issued_at,
    )
    credential = Credential(
        person_id=person_id,
        tokens=[t.hex() for t in tokens],
        count=len(tokens),
        commitment=commitment.hex(),
        issued_at=issued_at,
        lab_sig=crypto.sign(lab_seed, crypto.CREDENTIAL, credential_signed_fields(unsigned)),
        commit_salt=salt.hex() if salt is not None else None,
    )

    claims = []
    for g, finding in sorted(pairs):
        fields = {
            "person_id": person_id,
            "gene": g,
            "finding": finding,
            "salt": secrets.token_hex(16),
            "holder_pubkey": holder_hex,
            "issued_at": issued_at,
        }
        claims.append(
            SignedClaim(**fields, lab_sig=crypto.sign(lab_seed, crypto.CLAIM, fields))
        )
    return credential, claims
