"""Centralized token storage helpers for CLI/front-end components."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    import keyring  # type: ignore
    KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover - keyring optional
    KEYRING_AVAILABLE = False

TOKEN_FILE = Path.home() / ".jarvis_token.json"


def save_token(token: str) -> None:
    """Persist token to keyring or fallback file."""
    if KEYRING_AVAILABLE:
        try:
            keyring.set_password("jarvis", "jwt_token", token)
            return
        except Exception:
            pass

    TOKEN_FILE.write_text(json.dumps({"jarvis_token": token}))


def load_token() -> Optional[str]:
    """Load token from keyring or fallback file."""
    if KEYRING_AVAILABLE:
        try:
            token = keyring.get_password("jarvis", "jwt_token")
            if token:
                return token
        except Exception:
            pass

    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text()).get("jarvis_token")
        except Exception:
            return None
    return None


def delete_token() -> None:
    """Remove persisted token if present."""
    if KEYRING_AVAILABLE:
        try:
            keyring.delete_password("jarvis", "jwt_token")
        except Exception:
            pass
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
        except Exception:
            pass


def is_expiring(token: str, slack_seconds: int = 300) -> bool:
    """Return True if token is expired or will expire within the slack window."""
    try:
        # Use jose to inspect claims without verifying signature.
        from jose import jwt  # imported lazily to avoid heavy dependency at module import

        claims = jwt.get_unverified_claims(token)
        exp = claims.get("exp")
        if exp is None:
            return True
        return (exp - time.time()) <= slack_seconds
    except Exception:
        return True
