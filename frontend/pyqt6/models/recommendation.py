"""
JARVIS Recommendation Model
Data model for recommendations.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Recommendation:
    """Represents a recommendation item."""
    
    title: str
    description: str
    source: str
    relevance_score: float
    url: Optional[str] = None
    tags: Optional[List[str]] = None

