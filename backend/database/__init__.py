from .sqlite import SQLite
from .qdrant_client import QdrantManager
from .repository import Repository, Hit
from .data_collector import FileCollector, BrowserHistoryCollector, ActiveApplicationCollector

# Backward compatibility alias
SQLiteMeta = SQLite

__all__ = [
    "SQLite", "SQLiteMeta", "QdrantManager", "Repository", "Hit",
    "FileCollector", "BrowserHistoryCollector", "ActiveApplicationCollector"
]
