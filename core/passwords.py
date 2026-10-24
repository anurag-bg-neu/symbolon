"""Password hashing for the demo accounts (wallet and lab).

Salted scrypt from the standard library (memory-hard). Pure: no I/O, no logging.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return {"salt": salt.hex(), "hash": digest.hex(), **SCRYPT}


def verify_password(password: str, record: dict[str, Any]) -> bool:
    """True only for the right password. Never raises."""
    try:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(record["salt"]),
                                dklen=32, n=record["n"], r=record["r"], p=record["p"])
        return hmac.compare_digest(digest.hex(), record["hash"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
