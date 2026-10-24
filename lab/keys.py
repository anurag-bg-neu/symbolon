"""Demo lab keys and panel loading. Keys live in var/lab_keys.json (gitignored)."""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
from pathlib import Path

from core import crypto

ROOT = Path(__file__).resolve().parents[1]


def var_dir() -> Path:
    """GENEMATCH_VAR if set, else <repo>/var. Read at call time so tests can override it."""
    return Path(os.environ.get("GENEMATCH_VAR") or ROOT / "var")


def write_private(path: Path, obj: dict) -> None:
    """Write JSON readable only by the owner (chmod 600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(path, 0o600)


def load_or_create(path: Path | None = None) -> tuple[bytearray, bytes]:
    """Return (k_lab, ed25519_seed), creating the key file on first use."""
    path = Path(path) if path else var_dir() / "lab_keys.json"
    if not path.exists():
        write_private(path, {"k_lab": crypto.random_scalar().hex(),
                             "ed25519_seed": secrets.token_bytes(32).hex()})
    data = json.loads(path.read_text())
    return bytearray(bytes.fromhex(data["k_lab"])), bytes.fromhex(data["ed25519_seed"])


def load_panel() -> list[dict]:
    """data/panel.json (P3) if present, else the demo PANEL in scripts/demo_dummy_data.py."""
    panel_file = ROOT / "data" / "panel.json"
    if panel_file.exists():
        return json.loads(panel_file.read_text())
    spec = importlib.util.spec_from_file_location(
        "demo_dummy_data", ROOT / "scripts" / "demo_dummy_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.PANEL)
