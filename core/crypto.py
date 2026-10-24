"""Crypto primitives for GeneMatch.

ristretto255 via rbcl (libsodium), Ed25519 via PyNaCl, SHA-2 via hashlib.
This is the only module that imports the group library. Pure functions, no I/O,
no logging.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import nacl.exceptions
import nacl.signing
import rbcl

from core.models import Mode, ProtocolError, Role

# --- Domain-separation prefixes (exact values from design.md) ----------------

GENE = b"genematch/v1/gene/"
COMMIT = b"genematch/v1/commit/"
BLINDED = b"genematch/v1/blinded/"
CREDENTIAL = b"genematch/v1/credential/"
CLAIM = b"genematch/v1/claim/"
CLAIM_HASH = b"genematch/v1/claim-hash/"
PRESENT = b"genematch/v1/present/"

POINT_BYTES = 32
SCALAR_BYTES = 32
SALT_BYTES = 16
IDENTITY = bytes(POINT_BYTES)

# --- Hex helpers -------------------------------------------------------------


def to_hex(data: bytes | bytearray) -> str:
    return bytes(data).hex()


def from_hex(value: str, n_bytes: int | None = None) -> bytes:
    """Decode lowercase hex, optionally requiring an exact byte length."""
    if not isinstance(value, str):
        raise TypeError("hex value must be a str")
    if value != value.lower():
        raise ValueError("hex must be lowercase")
    raw = bytes.fromhex(value)  # raises ValueError on non-hex or odd length
    if n_bytes is not None and len(raw) != n_bytes:
        raise ValueError(f"expected {n_bytes} bytes")
    return raw


# --- Canonical encoding ------------------------------------------------------


def _normalize(obj: Any) -> Any:
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).hex()
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical encoding")
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("canonical encoding requires string keys")
            out[k] = _normalize(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    raise TypeError(f"type {type(obj).__name__} is not allowed in canonical encoding")


def canon(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8, bytes as lowercase hex."""
    return json.dumps(
        _normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# --- Group operations --------------------------------------------------------


def validate_point(p: bytes) -> bytes:
    """Return p if it is a valid, non-identity ristretto255 encoding.

    libsodium's is_valid_point accepts the identity, so it is rejected here.
    """
    if not isinstance(p, bytes) or len(p) != POINT_BYTES:
        raise ProtocolError("INVALID_POINT", "length")
    if p == IDENTITY:
        raise ProtocolError("INVALID_POINT", "identity")
    if not rbcl.crypto_core_ristretto255_is_valid_point(p):
        raise ProtocolError("INVALID_POINT", "encoding")
    return p


def point_from_hex(value: str) -> bytes:
    """Decode and validate a received point."""
    try:
        raw = from_hex(value, POINT_BYTES)
    except (TypeError, ValueError):
        raise ProtocolError("INVALID_POINT", "hex") from None
    return validate_point(raw)


def hash_to_group(gene_id: str) -> bytes:
    """H(g) = ristretto255_from_hash(SHA-512("genematch/v1/gene/" || g))."""
    if not isinstance(gene_id, str) or not gene_id:
        raise ValueError("gene_id must be a non-empty str")
    digest = hashlib.sha512(GENE + gene_id.encode("utf-8")).digest()
    return rbcl.crypto_core_ristretto255_from_hash(digest)


def random_scalar() -> bytearray:
    """Fresh non-zero scalar from libsodium, in a mutable buffer for zeroing."""
    return bytearray(rbcl.crypto_core_ristretto255_scalar_random())


def mul(scalar: bytes | bytearray, point: bytes) -> bytes:
    """scalar · point, validating the input point and the result."""
    if not isinstance(scalar, (bytes, bytearray)) or len(scalar) != SCALAR_BYTES:
        raise ValueError("scalar must be 32 bytes")
    validate_point(point)
    try:
        # bytes(...) makes a short-lived copy; the binding requires bytes.
        out = rbcl.crypto_scalarmult_ristretto255(bytes(scalar), point)
    except (RuntimeError, ValueError, TypeError):
        # libsodium fails when the result would be the identity.
        raise ProtocolError("INVALID_POINT", "scalarmult") from None
    return validate_point(out)


def blind_set(scalar: bytes | bytearray, points: list[bytes]) -> list[bytes]:
    return [mul(scalar, p) for p in points]


def zero_buffer(buf: bytearray) -> None:
    """Overwrite a mutable buffer in place."""
    for i in range(len(buf)):
        buf[i] = 0


# --- Commitments -------------------------------------------------------------


def commit_tokens(tokens: list[bytes], salt: bytes | None = None) -> bytes:
    """SHA-256(COMMIT || (salt or b"") || concat(sorted tokens)).

    `salt` is 16 raw bytes, never the stored hex string.
    """
    if salt is not None:
        if not isinstance(salt, bytes):
            raise TypeError("salt must be raw bytes, not hex")
        if len(salt) != SALT_BYTES:
            raise ValueError(f"salt must be exactly {SALT_BYTES} bytes")
    for t in tokens:
        if not isinstance(t, bytes) or len(t) != POINT_BYTES:
            raise ValueError("tokens must be 32-byte encodings")
    return sha256(COMMIT + (salt or b"") + b"".join(sorted(tokens)))


def commit_blinded(
    points: list[bytes], *, session_id: str, mode: Mode, role: Role
) -> bytes:
    """Round-0 commitment, bound to the session, the mode, and the sender's role.

    Input is raw points; canon runs over their lowercase hex list.
    """
    for p in points:
        if not isinstance(p, bytes) or len(p) != POINT_BYTES:
            raise ValueError("points must be 32-byte encodings")
    body = canon(
        {
            "session_id": session_id,
            "mode": mode,
            "role": role,
            "blinded": [p.hex() for p in points],
        }
    )
    return sha256(BLINDED + body)


# --- Signatures --------------------------------------------------------------


def pubkey_from_seed(seed: bytes) -> bytes:
    return bytes(nacl.signing.SigningKey(seed).verify_key)


def sign(seed: bytes, prefix: bytes, obj: Any) -> str:
    """Ed25519 signature over prefix || canon(obj), as hex."""
    key = nacl.signing.SigningKey(seed)
    return key.sign(prefix + canon(obj)).signature.hex()


def verify(pubkey: bytes | str, prefix: bytes, obj: Any, sig: bytes | str) -> bool:
    """True only for a valid signature. Never raises."""
    try:
        pk = from_hex(pubkey, 32) if isinstance(pubkey, str) else bytes(pubkey)
        sg = from_hex(sig, 64) if isinstance(sig, str) else bytes(sig)
        if len(pk) != 32 or len(sg) != 64:
            return False
        nacl.signing.VerifyKey(pk).verify(prefix + canon(obj), sg)
        return True
    except (nacl.exceptions.BadSignatureError, nacl.exceptions.CryptoError,
            ValueError, TypeError):
        return False
