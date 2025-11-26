"""
Database connection and initialization module
"""

import os
import sys
from pathlib import Path

# Add the current directory to Python path for relative imports
current_dir = Path(__file__).parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Initialize variables
SQLite = None
QdrantManager = None

# Try to import SQLite
try:
    from sqlite import SQLite
    print("SQLite imported successfully")
except ImportError as e:
    print(f"Warning: Could not import SQLite: {e}")
    SQLite = None

# Try to import QdrantManager (optional)
try:
    from qdrant_client import QdrantManager
    print("QdrantManager imported successfully")
except ImportError as e:
    print(f"Warning: Could not import QdrantManager: {e}")
    print("Qdrant vector database features will be disabled")
    QdrantManager = None

def create_tables():
    """Initialize database tables"""
    try:
        # Initialize SQLite database
        if SQLite is not None:
            try:
                sqlite_db = SQLite()
                print("SQLite tables initialized")
            except Exception as sqlite_error:
                print(f"SQLite initialization failed: {sqlite_error}")
                return False
        else:
            print("SQLite not available")
            return False
        
        # Try to initialize Qdrant vector database (optional)
        if QdrantManager is not None:
            try:
                qdrant_manager = QdrantManager()
                print("Qdrant vector database initialized")
            except Exception as qdrant_error:
                print(f"Qdrant vector database initialization failed: {qdrant_error}")
                print("Qdrant server is not running. Vector search features will be limited.")
                print("To enable full functionality, start Qdrant server: docker run -p 6333:6333 qdrant/qdrant")
        else:
            print("Qdrant vector database disabled (QdrantManager not available)")
        
        return True
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        return False

def get_sqlite():
    """Get SQLite instance"""
    if SQLite is not None:
        return SQLite()
    else:
        raise ImportError("SQLite not available")

def get_qdrant_manager():
    """Get Qdrant manager instance"""
    if QdrantManager is not None:
        return QdrantManager()
    else:
        raise ImportError("QdrantManager not available")

# Backward compatibility aliases
get_sqlite_meta = get_sqlite
SQLiteMeta = SQLite

# If this file is run directly, initialize the database
if __name__ == "__main__":
    success = create_tables()
    if success:
        print("Database initialization completed successfully")
        exit(0)
    else:
        print("Database initialization failed")
        exit(1)
