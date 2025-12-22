"""
JARVIS Path Utilities
Resource path resolution for both development and PyInstaller builds.
"""

import sys
from pathlib import Path


def get_pyqt6_dir() -> Path:
    """Get the pyqt6 directory path."""
    if getattr(sys, 'frozen', False):
        # PyInstaller로 빌드된 경우
        return Path(sys._MEIPASS)
    else:
        # 일반 Python 실행 - utils 폴더의 부모 디렉토리
        return Path(__file__).resolve().parent.parent


def get_resource_path(relative_path: str) -> str:
    """
    PyInstaller 번들 또는 일반 실행 환경에서 리소스 경로를 반환합니다.
    
    Args:
        relative_path: 리소스의 상대 경로 (예: 'resources/icons/jarvis.ico')
    
    Returns:
        절대 경로 문자열
    """
    base_path = get_pyqt6_dir()
    return str(base_path / relative_path)


__all__ = ['get_resource_path', 'get_pyqt6_dir']

