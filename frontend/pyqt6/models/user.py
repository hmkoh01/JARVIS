"""
JARVIS User Model
Data model for user information.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    """Represents a user."""
    
    user_id: int
    email: Optional[str] = None
    name: Optional[str] = None
    
    @classmethod
    def from_token_claims(cls, claims: dict) -> "User":
        """Create User from JWT token claims."""
        return cls(
            user_id=claims.get("user_id", 0),
            email=claims.get("email"),
            name=claims.get("name")
        )

