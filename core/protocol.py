"""Two-party DH-PSI over lab tokens, with context-bound commit-then-reveal.

One `Session` per wallet per check. The session scalar lives only in this
object: it is never returned, serialized, or logged, and `zero()` overwrites it
on completion or on any error. See design.md, "Cryptographic construction".
"""

from __future__ import annotations

import functools
import hmac
import secrets
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from core import crypto
from core.issuance import credential_signed_fields
from core.models import (
    MAX_SET,
    BlindedSet,
    Commitment,
    Credential,
    DoubleBlinded,
    Mode,
    ProtocolError,
    Result,
    Role,
)

NEW = "NEW"
COMMITTED = "COMMITTED"
PEER_COMMITTED = "PEER_COMMITTED"
REVEALED = "REVEALED"
DOUBLE_SENT = "DOUBLE_SENT"
DONE = "DONE"
FAILED = "FAILED"

_OPPOSITE: dict[str, Role] = {"initiator": "joiner", "joiner": "initiator"}
_rng = secrets.SystemRandom()

F = TypeVar("F", bound=Callable[..., Any])


def _prefix(p: bytes) -> str:
    return p.hex()[:8]


def _guarded(fn: F) -> F:
    """Zero the scalar and mark FAILED on any error, then re-raise."""

    @functools.wraps(fn)
    def wrapper(self: "Session", *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(self, *args, **kwargs)
        except ProtocolError:
            self._fail()
            raise
        except ValidationError:
            # Malformed peer message (bad hex, wrong type, missing or extra field).
            self._fail()
            raise ProtocolError("MALFORMED_MESSAGE", "schema") from None
        except Exception:
            self._fail()
            raise

    return wrapper  # type: ignore[return-value]


class Session:
    def __init__(
        self,
        role: Role,
        mode: Mode,
        session_id: str,
        credential: Credential | dict,
        genes: list[str],
        lab_pubkey: bytes | str,
    ) -> None:
        if role not in _OPPOSITE:
            raise ValueError("role must be 'initiator' or 'joiner'")
        if mode not in ("count", "detail"):
            raise ValueError("mode must be 'count' or 'detail'")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required")

        self.role: Role = role
        self.mode: Mode = mode
        self.session_id = session_id
        self.state = NEW
        self._scalar: bytearray | None = None
        self._x: list[bytes] = []        # own blinded set, in sent order
        self._perm: list[int] = []       # _x[i] = a · tokens[_perm[i]]
        self._z: set[bytes] = set()      # a · (peer's blinded points)
        self._peer_h: bytes | None = None
        self._seen: dict[str, Any] = {"sent": {}, "received": {}}

        try:
            self._lab_pubkey = (
                crypto.from_hex(lab_pubkey, 32) if isinstance(lab_pubkey, str)
                else bytes(lab_pubkey)
            )
            cred = (
                credential if isinstance(credential, Credential)
                else Credential.model_validate(credential)
            )
        except (ValueError, TypeError, ValidationError):
            self.state = FAILED
            raise ProtocolError("BAD_CREDENTIAL", "format") from None

        self._credential = cred
        self._genes = list(genes)
        self._tokens = self._check_own_credential(cred, self._genes)

    # --- owner-side credential check --------------------------------------

    def _check_own_credential(self, cred: Credential, genes: list[str]) -> list[bytes]:
        def bad(reason: str) -> ProtocolError:
            self.state = FAILED
            return ProtocolError("BAD_CREDENTIAL", reason)

        # 1. Lab signature first: if it fails, nothing else in the file is trusted.
        if not crypto.verify(
            self._lab_pubkey, crypto.CREDENTIAL, credential_signed_fields(cred), cred.lab_sig
        ):
            raise bad("lab_sig")
        # 2. Lengths agree.
        if not (len(genes) == cred.count == len(cred.tokens)):
            raise bad("length")
        # 3. Genes sorted and unique (tokens[i] belongs to genes[i]).
        if genes != sorted(set(genes)):
            raise bad("genes")
        # 4. Salt, if present, is exactly 16 bytes.
        salt = None
        if cred.commit_salt is not None:
            try:
                salt = crypto.from_hex(cred.commit_salt, crypto.SALT_BYTES)
            except (ValueError, TypeError):
                raise bad("salt") from None
        # 5. Every token is a valid point.
        try:
            tokens = [crypto.point_from_hex(t) for t in cred.tokens]
        except ProtocolError:
            raise bad("token") from None
        # 6. Tokens and salt reproduce the signed commitment.
        if not hmac.compare_digest(
            crypto.commit_tokens(tokens, salt), crypto.from_hex(cred.commitment, 32)
        ):
            raise bad("commitment")
        return tokens

    # --- helpers -----------------------------------------------------------

    def _require(self, state: str) -> None:
        if self.state != state:
            raise ProtocolError("OUT_OF_ORDER", f"expected {state}, in {self.state}")

    def _fail(self) -> None:
        self.state = FAILED
        self.zero()

    def _commit(self, points: list[bytes], role: Role) -> bytes:
        return crypto.commit_blinded(
            points, session_id=self.session_id, mode=self.mode, role=role
        )

    # --- rounds ------------------------------------------------------------

    @_guarded
    def round0(self) -> Commitment:
        """Sample the scalar, blind and shuffle own tokens, return the commitment."""
        self._require(NEW)
        self._scalar = crypto.random_scalar()
        perm = list(range(len(self._tokens)))
        _rng.shuffle(perm)
        self._perm = perm
        self._x = [crypto.mul(self._scalar, self._tokens[i]) for i in perm]
        h = self._commit(self._x, self.role)
        self._seen["sent"]["round0"] = _prefix(h)
        self.state = COMMITTED
        return Commitment(h=h.hex())

    @_guarded
    def receive_commitment(self, c: Commitment | dict) -> None:
        self._require(COMMITTED)
        c = c if isinstance(c, Commitment) else Commitment.model_validate(c)
        self._peer_h = crypto.from_hex(c.h, 32)
        self._seen["received"]["round0"] = _prefix(self._peer_h)
        self.state = PEER_COMMITTED

    @_guarded
    def round1(self) -> BlindedSet:
        """Reveal own blinded set with the lab-signed metadata."""
        self._require(PEER_COMMITTED)
        cred = self._credential
        msg = BlindedSet(
            blinded=[p.hex() for p in self._x],
            person_id=cred.person_id,
            count=cred.count,
            commitment=cred.commitment,
            issued_at=cred.issued_at,
            lab_sig=cred.lab_sig,
        )
        self._seen["sent"]["round1"] = {
            "count": cred.count,
            "points": [_prefix(p) for p in self._x],
        }
        self.state = REVEALED
        return msg

    @_guarded
    def verify_peer(self, peer: BlindedSet | dict) -> list[bytes]:
        """Check the peer's round-1 message; return its validated points."""
        if self._peer_h is None:
            raise ProtocolError("OUT_OF_ORDER", "no peer commitment")
        peer = peer if isinstance(peer, BlindedSet) else BlindedSet.model_validate(peer)
        raw = [crypto.from_hex(p, crypto.POINT_BYTES) for p in peer.blinded]

        # Commitment, recomputed with our session and mode and the peer's role.
        expected = self._commit(raw, _OPPOSITE[self.role])
        if not hmac.compare_digest(expected, self._peer_h):
            raise ProtocolError("COMMITMENT_MISMATCH")
        if not crypto.verify(
            self._lab_pubkey, crypto.CREDENTIAL, credential_signed_fields(peer), peer.lab_sig
        ):
            raise ProtocolError("BAD_SIGNATURE")
        if peer.count > MAX_SET or peer.count < 0:
            raise ProtocolError("SET_TOO_LARGE")
        if len(raw) != peer.count:
            raise ProtocolError("COUNT_MISMATCH")
        for p in raw:
            crypto.validate_point(p)
        if len(set(raw)) != len(raw):
            raise ProtocolError("INVALID_POINT", "duplicate")
        return raw

    @_guarded
    def round2(self, peer: BlindedSet | dict) -> DoubleBlinded:
        """Verify the peer's blinded set and return a · X_peer."""
        self._require(REVEALED)
        raw = self.verify_peer(peer)
        assert self._scalar is not None
        z = [crypto.mul(self._scalar, x) for x in raw]
        self._z = set(z)
        out = list(z)
        if self.mode == "count":
            _rng.shuffle(out)
        self._seen["received"]["round1"] = {
            "count": len(raw),
            "points": [_prefix(p) for p in raw],
        }
        self._seen["sent"]["round2"] = {
            "count": len(out),
            "points": [_prefix(p) for p in out],
        }
        self.state = DOUBLE_SENT
        return DoubleBlinded(points=[p.hex() for p in out])

    @_guarded
    def result(self, peer_double: DoubleBlinded | dict) -> Result:
        """Intersect b · X_own with a · X_peer locally, then zero the scalar."""
        self._require(DOUBLE_SENT)
        peer_double = (
            peer_double if isinstance(peer_double, DoubleBlinded)
            else DoubleBlinded.model_validate(peer_double)
        )
        y = [crypto.point_from_hex(p) for p in peer_double.points]
        if len(y) != len(self._x):
            raise ProtocolError("COUNT_MISMATCH", "round2")
        if len(set(y)) != len(y):
            raise ProtocolError("INVALID_POINT", "duplicate")
        self._seen["received"]["round2"] = {
            "count": len(y),
            "points": [_prefix(p) for p in y],
        }

        if self.mode == "detail":
            shared = sorted(
                self._genes[self._perm[i]] for i, p in enumerate(y) if p in self._z
            )
            res = Result(mode="detail", shared_count=len(shared), shared_genes=shared)
        else:
            res = Result(
                mode="count", shared_count=len(set(y) & self._z), shared_genes=None
            )
        self.zero()
        self.state = DONE
        return res

    # --- cleanup and transparency -----------------------------------------

    def zero(self) -> None:
        """Overwrite the scalar in place and drop intermediate state. Idempotent.

        Best-effort: Python and the binding may hold short-lived copies.
        """
        if self._scalar is not None:
            crypto.zero_buffer(self._scalar)
        self._x = []
        self._perm = []
        self._z = set()

    def scalar_is_zeroed(self) -> bool:
        """For tests: True if no live scalar remains."""
        return self._scalar is None or not any(self._scalar)

    def transcript(self) -> dict[str, Any]:
        """What this wallet sent and received: 8-hex prefixes and counts only."""
        return {
            "role": self.role,
            "mode": self.mode,
            "state": self.state,
            "sent": dict(self._seen["sent"]),
            "received": dict(self._seen["received"]),
        }
