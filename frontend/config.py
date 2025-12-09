"""
JARVIS Frontend Configuration
configs.yaml에서 설정을 읽어 프론트엔드 전역에서 사용합니다.
"""

import os
import sys
import yaml
from pathlib import Path


def _get_base_path():
    """PyInstaller 번들 또는 일반 실행 환경에 따른 기본 경로 반환"""
    if getattr(sys, 'frozen', False):
        # PyInstaller로 빌드된 exe 실행 중
        # sys._MEIPASS는 번들된 리소스가 추출되는 임시 디렉토리
        return Path(sys._MEIPASS)
    else:
        # 일반 Python 실행 - frontend/config.py의 상위 디렉토리(프로젝트 루트)
        return Path(__file__).parent.parent


def _load_configs():
    """configs.yaml 파일에서 설정을 로드합니다."""
    base_path = _get_base_path()
    config_path = base_path / "configs.yaml"
    
    if not config_path.exists():
        print(f"⚠️ configs.yaml 파일을 찾을 수 없습니다: {config_path}")
        print("   기본값을 사용합니다.")
        return {}
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            configs = yaml.safe_load(f)
            print(f"✅ configs.yaml 로드 완료: {config_path}")
            return configs
    except Exception as e:
        print(f"⚠️ configs.yaml 로드 실패: {e}")
        return {}


# configs.yaml 로드
_configs = _load_configs()

# =============================================================================
# API 설정
# =============================================================================

# API 설정 가져오기 (없으면 기본값 사용)
_api_config = _configs.get("api", {})

# 백엔드 API URL
API_BASE_URL = _api_config.get("base_url", "http://localhost:8000")

# OAuth 콜백 포트
CALLBACK_PORT = _api_config.get("callback_port", 9090)
CALLBACK_URI = f"http://127.0.0.1:{CALLBACK_PORT}/auth/callback"

# =============================================================================
# WebSocket 설정
# =============================================================================

def get_websocket_url():
    """WebSocket URL 반환 (http -> ws, https -> wss)"""
    if API_BASE_URL.startswith("https://"):
        return API_BASE_URL.replace("https://", "wss://")
    return API_BASE_URL.replace("http://", "ws://")

WS_BASE_URL = get_websocket_url()

# =============================================================================
# 기타 설정 (configs.yaml에서 로드)
# =============================================================================

# Qdrant 설정
QDRANT_URL = _configs.get("qdrant", {}).get("url", "http://localhost:6333")
QDRANT_COLLECTION = _configs.get("qdrant", {}).get("collection_name", "user_context")

# 임베딩 설정
EMBEDDING_MODEL = _configs.get("embedding", {}).get("model_name", "BAAI/bge-m3")


# =============================================================================
# 디버깅용 출력
# =============================================================================
if __name__ == "__main__":
    print("=== JARVIS Frontend Config ===")
    print(f"API_BASE_URL: {API_BASE_URL}")
    print(f"WS_BASE_URL: {WS_BASE_URL}")
    print(f"CALLBACK_PORT: {CALLBACK_PORT}")
    print(f"CALLBACK_URI: {CALLBACK_URI}")
    print(f"QDRANT_URL: {QDRANT_URL}")
