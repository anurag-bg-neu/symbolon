"""Wire models, shared constants, and the protocol error type.

All byte values on the wire are lowercase hex strings. Shapes follow the
"Data models" table in .kiro/specs/genematch/design.md.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

# --- Constants ---------------------------------------------------------------

MAX_SET = 20
CLAIM_GENES: frozenset[str] = frozenset({"BRCA1", "BRCA2"})
CLAIM_FINDING = "germline pathogenic variant"

Mode = Literal["count", "detail"]
Role = Literal["initiator", "joiner"]

# --- Hex field types ---------------------------------------------------------

_HEX_RE = re.compile(r"^(?:[0-9a-f]{2})*$")


def _hex(value: str) -> str:
    if not _HEX_RE.fullmatch(value):
        raise ValueError("must be lowercase hex with an even number of characters")
    return value


def _hex_len(n_bytes: int):
    def check(value: str) -> str:
        _hex(value)
        if len(value) != 2 * n_bytes:
            raise ValueError(f"must be {n_bytes} bytes of hex")
        return value

    return check


Hex = Annotated[str, AfterValidator(_hex)]
Hex16 = Annotated[str, AfterValidator(_hex_len(16))]
Hex32 = Annotated[str, AfterValidator(_hex_len(32))]
Hex64 = Annotated[str, AfterValidator(_hex_len(64))]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --- Credentials and claims --------------------------------------------------


class Credential(_Model):
    person_id: str
    tokens: list[Hex32]
    count: int
    commitment: Hex32
    issued_at: str
    lab_sig: Hex64
    # Wallet-only. Not signed and never sent to the relay or partner.
    commit_salt: Hex16 | None = None


class SignedClaim(_Model):
    person_id: str
    gene: str
    finding: str
    salt: Hex16
    holder_pubkey: Hex32
    issued_at: str
    lab_sig: Hex64


# --- Protocol messages -------------------------------------------------------


class Commitment(_Model):
    """Round 0."""

    h: Hex32


class BlindedSet(_Model):
    """Round 1."""

    blinded: list[Hex32]
    person_id: str
    count: int
    commitment: Hex32
    issued_at: str
    lab_sig: Hex64


class DoubleBlinded(_Model):
    """Round 2."""

    points: list[Hex32]


class Result(_Model):
    mode: Mode
    shared_count: int
    shared_genes: list[str] | None  # always None in Count mode


# --- Panel, counselor, trials ------------------------------------------------


class PanelEntry(_Model):
    gene: str
    condition: str
    inheritance: Literal["AR"]
    summary: str
    medlineplus: str


class Explanation(_Model):
    summary: str
    what_it_means: str
    next_steps: list[str]
    questions: list[str]
    disclaimer: str
    source: Literal["llm", "template"]


class TrialRequest(_Model):
    request_id: str
    trial_id: str
    nonce: Hex
    gene: str
    finding: str
    expires_at: str


class Presentation(_Model):
    claim: SignedClaim
    trial_id: str
    nonce: Hex
    holder_sig: Hex64


# --- Errors ------------------------------------------------------------------

USER_MESSAGES: dict[str, str] = {
    "BAD_SIGNATURE": (
        "Your partner's results couldn't be verified as coming from the lab, "
        "so the check was stopped. Nothing about you was revealed."
    ),
    "COMMITMENT_MISMATCH": (
        "Your partner's data didn't match what they locked in at the start, "
        "so the check was stopped. Nothing about you was revealed."
    ),
    "COUNT_MISMATCH": (
        "Your partner sent a different number of results than the lab certified, "
        "so the check was stopped. Nothing about you was revealed."
    ),
    "SET_TOO_LARGE": (
        "Your partner's results were larger than the lab allows, "
        "so the check was stopped. Nothing about you was revealed."
    ),
    "INVALID_POINT": (
        "Your partner's data was damaged or invalid, "
        "so the check was stopped. Nothing about you was revealed."
    ),
    "PEER_DECLINED": (
        "Your partner declined the check. No data was exchanged."
    ),
    "SESSION_EXPIRED": (
        "This check expired before it finished, and its data was deleted. "
        "Nothing about you was revealed."
    ),
    "RELAY_UNAVAILABLE": (
        "We couldn't reach the server that connects you with your partner. "
        "Nothing about you was revealed. Please try again."
    ),
    "OUT_OF_ORDER": (
        "The check received steps in the wrong order, so it was stopped. "
        "Nothing about you was revealed."
    ),
    "BAD_CREDENTIAL": (
        "Your own lab results on this device couldn't be verified, "
        "so the check was stopped before anything was sent."
    ),
    "BAD_LAB_SIG": (
        "This result couldn't be verified as coming from the lab, so it was rejected."
    ),
    "BAD_HOLDER_SIG": (
        "This result couldn't be confirmed as belonging to the person presenting it, "
        "so it was rejected."
    ),
    "NONCE_REUSED": (
        "This request was already used. Please start a new request."
    ),
    "NONCE_EXPIRED": (
        "This request expired. Please start a new request."
    ),
    "WRONG_FINDING": (
        "The result shared doesn't match what this trial requires, so it was rejected."
    ),
    "NO_MATCHING_CLAIM": (
        "Your wallet has no lab result matching this request, so nothing was sent."
    ),
    "REQUEST_MISMATCH": (
        "The result shared was made for a different request, so it was rejected."
    ),
}


class ProtocolError(Exception):
    """A protocol failure with a user-safe message.

    `reason` is for tests and debugging only. It is never shown to users and
    must never contain scalars, tokens, gene IDs, or credential contents.
    """

    def __init__(self, code: str, reason: str | None = None) -> None:
        if code not in USER_MESSAGES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.reason = reason
        self.user_message = USER_MESSAGES[code]
        super().__init__(code if reason is None else f"{code} ({reason})")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "user_message": self.user_message}
