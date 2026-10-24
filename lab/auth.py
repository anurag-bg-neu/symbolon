"""Demo lab sign-in: one account per synthetic patient, plus one lab staff account.

Demo-grade: scrypt-hashed passwords, in-memory sessions, no rate limiting or lockout.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.passwords import hash_password, verify_password

COOKIE = "gm_lab_session"
SESSION_TTL = 8 * 3600
STAFF_ID = "_staff"
LAB_DOMAIN = "demolab.test"  # separate from wallet accounts (@genematch.demo)
STAFF_EMAIL = f"staff@{LAB_DOMAIN}"
STAFF_PASSWORD = "lab-staff"


def patient_email(person_id: str) -> str:
    return f"{person_id}@{LAB_DOMAIN}"


def patient_password(person_id: str) -> str:
    """Published on the lab sign-in page for the demo."""
    return f"lab-{person_id}"


def make_accounts(person_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    accounts = {pid: {"email": patient_email(pid), "role": "patient", "person_id": pid,
                      **hash_password(patient_password(pid))} for pid in person_ids}
    accounts[STAFF_ID] = {"email": STAFF_EMAIL, "role": "staff", "person_id": None,
                          **hash_password(STAFF_PASSWORD)}
    return accounts


def load_accounts(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def authenticate(accounts: dict[str, dict[str, Any]], email: str,
                 password: str) -> dict[str, Any] | None:
    """The matching account, or None. Wrong password and unknown email look the same."""
    email = (email or "").strip().lower()
    for rec in accounts.values():
        if rec.get("email", "").lower() == email:
            return rec if verify_password(password or "", rec) else None
    hash_password(password or "")  # similar timing for unknown emails
    return None


class LabSessions:
    """token -> (expiry, {role, person_id}), in memory."""

    def __init__(self, ttl: int = SESSION_TTL, clock=time.time) -> None:
        self.ttl = ttl
        self.clock = clock
        self._s: dict[str, tuple[float, dict[str, Any]]] = {}

    def create(self, identity: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        self._s[token] = (self.clock() + self.ttl, dict(identity))
        return token

    def get(self, token: str | None) -> dict[str, Any] | None:
        if not token or token not in self._s:
            return None
        exp, ident = self._s[token]
        if self.clock() > exp:
            self._s.pop(token, None)
            return None
        return dict(ident)

    def revoke(self, token: str | None) -> None:
        if token:
            self._s.pop(token, None)

    def clear(self) -> None:
        self._s.clear()


def same_origin(request) -> bool:
    """Same CSRF rule as the wallet: no Origin (server-side callers) or our own origin."""
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")
    host = request.headers.get("host", "")
    return origin in (f"http://{host}", f"https://{host}")
