"""
JARVIS File Controller
Handles file upload and folder indexing operations.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from typing import List


class FileController(QObject):
    """Controller for file operations."""
    
    # Signals for UI updates (thread-safe)
    upload_progress = pyqtSignal(int)  # percentage
    upload_complete = pyqtSignal(bool, str)  # success, message
    index_progress = pyqtSignal(str)  # status message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # TODO: Phase 2 - Initialize API client
    
    def upload_files(self, file_paths: List[str]) -> None:
        """Upload files to the backend."""
        # TODO: Phase 2 - Implement file upload in QThread
        pass
    
    def index_folders(self, folder_paths: List[str]) -> None:
        """Request folder indexing."""
        # TODO: Phase 2 - Implement folder indexing request
        pass

