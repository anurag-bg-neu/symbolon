"""Selective disclosure of one lab-signed finding, bound to the holder's key.

Nonce reuse and expiry are enforced by the trial service, which owns the
nonce store; this module checks signatures and that the presentation answers
the given request.
"""

from __future__ import annotations

from collections.abc import Iterable

from core import crypto
from core.issuance import claim_signed_fields
from core.models import Presentation, ProtocolError, SignedClaim, TrialRequest


def claim_hash(claim: SignedClaim) -> str:
    """SHA-256(CLAIM_HASH || canon(claim)), including lab_sig, as hex."""
    return crypto.sha256(crypto.CLAIM_HASH + crypto.canon(claim.model_dump())).hex()


def _present_body(claim: SignedClaim, nonce: str, trial_id: str) -> dict[str, str]:
    return {"claim_hash": claim_hash(claim), "nonce": nonce, "trial_id": trial_id}


def select_claim(claims: Iterable[SignedClaim], gene: str, finding: str) -> SignedClaim:
    """Exactly one claim matching the request, or NO_MATCHING_CLAIM."""
    for c in claims:
        if c.gene == gene and c.finding == finding:
            return c
    raise ProtocolError("NO_MATCHING_CLAIM")


def make_presentation(
    claim: SignedClaim, holder_seed: bytes, nonce: str, trial_id: str
) -> Presentation:
    if crypto.pubkey_from_seed(holder_seed).hex() != claim.holder_pubkey:
        raise ProtocolError("BAD_HOLDER_SIG", "seed does not match claim")
    sig = crypto.sign(holder_seed, crypto.PRESENT, _present_body(claim, nonce, trial_id))
    return Presentation(claim=claim, trial_id=trial_id, nonce=nonce, holder_sig=sig)


def verify_presentation(
    p: Presentation, lab_pubkey: bytes | str, request: TrialRequest
) -> None:
    """Raise ProtocolError unless the presentation is valid for this request."""
    claim = p.claim
    if not crypto.verify(lab_pubkey, crypto.CLAIM, claim_signed_fields(claim), claim.lab_sig):
        raise ProtocolError("BAD_LAB_SIG")
    if not crypto.verify(
        claim.holder_pubkey,
        crypto.PRESENT,
        _present_body(claim, p.nonce, p.trial_id),
        p.holder_sig,
    ):
        raise ProtocolError("BAD_HOLDER_SIG")
    if p.nonce != request.nonce or p.trial_id != request.trial_id:
        raise ProtocolError("REQUEST_MISMATCH")
    if claim.gene != request.gene or claim.finding != request.finding:
        raise ProtocolError("WRONG_FINDING")
