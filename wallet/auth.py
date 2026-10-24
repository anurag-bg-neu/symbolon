"""Demo-grade accounts and sessions for a wallet (one person per wallet port).

Passwords are stored as salted scrypt hashes (stdlib hashlib, memory-hard).
Sessions are random tokens held in memory per wallet process. Not production auth:
no rate limiting, no lockout, no persistence.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from core.passwords import SCRYPT, hash_password, verify_password  # noqa: F401

SESSION_TTL = 8 * 3600
DEMO_DOMAIN = "genematch.demo"


def demo_email(person_id: str) -> str:
    return f"{person_id}@{DEMO_DOMAIN}"


def demo_password(person_id: str) -> str:
    """Published on the sign-in page. Demo only."""
    return f"demo-{person_id}"


def make_account(person_id: str, display_name: str, port: int | None) -> dict[str, Any]:
    return {"email": demo_email(person_id), "display_name": display_name, "port": port,
            **hash_password(demo_password(person_id))}


def load_accounts(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


class Sessions:
    """In-memory session tokens for one wallet process."""

    def __init__(self, ttl: int = SESSION_TTL, clock=time.time) -> None:
        self.ttl = ttl
        self.clock = clock
        self._tokens: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = self.clock() + self.ttl
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        # Look up by constant-time comparison is unnecessary for a dict of random
        # 256-bit tokens; expiry is enforced here.
        exp = self._tokens.get(token)
        if exp is None:
            return False
        if self.clock() > exp:
            self._tokens.pop(token, None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)


class Accounts:
    """Sign-in rules for a wallet that belongs to exactly one person."""

    def __init__(self, owner: str, accounts: dict[str, dict[str, Any]]) -> None:
        self.owner = owner
        self.accounts = accounts

    def display_name(self, person_id: str | None = None) -> str:
        pid = person_id or self.owner
        return self.accounts.get(pid, {}).get("display_name") or pid.capitalize()

    def check(self, email: str, password: str) -> tuple[str, str | None]:
        """Return ("ok", None), ("bad_login", None) or ("wrong_wallet", other_person)."""
        email = (email or "").strip().lower()
        matched = None
        for pid, rec in self.accounts.items():
            if rec.get("email", "").lower() == email:
                matched = pid
                break
        if matched is None:
            hash_password(password or "")  # similar timing for unknown emails
            return "bad_login", None
        if not verify_password(password or "", self.accounts[matched]):
            return "bad_login", None
        if matched != self.owner:
            return "wrong_wallet", matched
        return "ok", None

    def wrong_wallet_message(self, other: str) -> str:
        port = self.accounts.get(other, {}).get("port")
        where = f" at localhost:{port}" if port else ""
        return (f"This wallet belongs to {self.display_name()}. "
                f"Open {self.display_name(other)}'s wallet{where}.")
