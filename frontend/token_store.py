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


def decode_token_claims(token: str) -> Optional[dict]:
    """Decode token and return claims without signature verification.
    
    Returns:
        dict with claims (user_id, exp, iat, etc.) or None if invalid.
    """
    try:
        from jose import jwt
        claims = jwt.get_unverified_claims(token)
        return claims
    except Exception:
        return None


def get_user_id_from_token(token: str) -> Optional[int]:
    """Extract user_id from token without signature verification.
    
    Returns:
        user_id as int, or None if token is invalid or missing user_id.
    """
    claims = decode_token_claims(token)
    if claims:
        user_id = claims.get("user_id")
        if user_id is not None:
            return int(user_id)
    return None


def get_valid_token_and_user() -> tuple[Optional[str], Optional[int]]:
    """Load token and extract user_id if token is valid and not expiring.
    
    Returns:
        (token, user_id) tuple. Both are None if no valid token found.
    """
    token = load_token()
    if not token:
        return None, None
    
    if is_expiring(token):
        return None, None
    
    user_id = get_user_id_from_token(token)
    if user_id is None:
        return None, None
    
    return token, user_id
