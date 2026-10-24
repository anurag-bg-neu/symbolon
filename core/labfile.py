"""Lab files: a lab-signed carrier credential plus its plain gene list.

The credential signature covers the tokens (via the commitment) but not the gene
names, so the lab also signs the gene list bound to that commitment. Pure: no I/O,
no logging. See design.md, "Onboarding: Connect your lab and lab files".
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from core import crypto
from core.issuance import claim_signed_fields, credential_signed_fields
from core.models import Credential, ProtocolError, SignedClaim

FORMAT = "genematch-lab-file/v1"

MESSAGES = {
    "NOT_LAB_FILE": "This isn't a Symbolon lab file. Nothing was changed.",
    "MALFORMED_FILE": "This lab file is damaged or incomplete. Nothing was changed.",
    "NOT_SIGNED": "This file isn't signed by the lab, so it can't be used. Nothing was changed.",
    "TAMPERED": "This file was changed after the lab signed it, so it can't be used. "
                "Nothing was changed.",
    "WRONG_PERSON": "This file belongs to {name}, not to the owner of this wallet. "
                    "Nothing was changed.",
    "BAD_CLAIMS": "The lab's trial findings couldn't be verified. Nothing was changed.",
}


class LabFileError(ValueError):
    """A rejected lab file. `user_message` is safe to show; it never contains data."""

    def __init__(self, code: str, **fmt: str) -> None:
        self.code = code
        self.user_message = MESSAGES[code].format(**fmt)
        super().__init__(code)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "user_message": self.user_message}


def _signed_fields(person_id: str, display_name: str, genes: list[str],
                   cred: Credential) -> dict[str, Any]:
    return {"format": FORMAT, "person_id": person_id, "display_name": display_name,
            "genes": list(genes), "count": cred.count, "commitment": cred.commitment,
            "issued_at": cred.issued_at}


def make_lab_file(lab_seed: bytes, credential: Credential, genes: list[str],
                  display_name: str) -> dict[str, Any]:
    """Wrap an issued credential and its sorted genes into a signed lab file."""
    genes = list(genes)
    if genes != sorted(set(genes)) or len(genes) != credential.count:
        raise ValueError("genes must be the sorted, unique genes the credential was issued for")
    fields = _signed_fields(credential.person_id, display_name, genes, credential)
    return {"format": FORMAT, "synthetic": True, "person_id": credential.person_id,
            "display_name": display_name, "genes": genes,
            "credential": credential.model_dump(),
            "genes_sig": crypto.sign(lab_seed, crypto.LAB_FILE, fields)}


def verify_lab_file(doc: Any, lab_pubkey: bytes | str,
                    person_id: str) -> tuple[Credential, list[str], str]:
    """Return (credential, genes, display_name) or raise LabFileError."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise LabFileError("NOT_LAB_FILE")
    try:
        cred = Credential.model_validate(doc["credential"])
        genes = doc["genes"]
        name = doc["display_name"]
        sig = doc["genes_sig"]
        if not (isinstance(genes, list) and all(isinstance(g, str) for g in genes)
                and isinstance(name, str) and isinstance(sig, str)
                and isinstance(doc.get("person_id"), str)):
            raise TypeError
    except (KeyError, TypeError, ValidationError):
        raise LabFileError("MALFORMED_FILE") from None

    if not crypto.verify(lab_pubkey, crypto.CREDENTIAL, credential_signed_fields(cred),
                         cred.lab_sig):
        raise LabFileError("NOT_SIGNED")
    if doc["person_id"] != cred.person_id or not crypto.verify(
            lab_pubkey, crypto.LAB_FILE, _signed_fields(cred.person_id, name, genes, cred), sig):
        raise LabFileError("TAMPERED")
    if cred.person_id != person_id:
        raise LabFileError("WRONG_PERSON", name=name)

    # The same owner-side check the wallet runs before every check.
    from core.protocol import Session
    try:
        s = Session("initiator", "count", "lab-file-check", cred, genes, lab_pubkey)
    except ProtocolError:
        raise LabFileError("TAMPERED") from None
    s.zero()
    return cred, list(genes), name


def verify_connect_claims(claims: Any, lab_pubkey: bytes | str, person_id: str,
                          holder_pubkey: str) -> list[SignedClaim]:
    """Claims from Connect: lab-signed, for this person, bound to our holder key."""
    if not isinstance(claims, list):
        raise LabFileError("BAD_CLAIMS")
    out = []
    for raw in claims:
        try:
            c = SignedClaim.model_validate(raw)
        except ValidationError:
            raise LabFileError("BAD_CLAIMS") from None
        if (c.person_id != person_id or c.holder_pubkey != holder_pubkey
                or not crypto.verify(lab_pubkey, crypto.CLAIM, claim_signed_fields(c),
                                     c.lab_sig)):
            raise LabFileError("BAD_CLAIMS")
        out.append(c)
    return out
