import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv
from pydantic_settings import BaseSettings

logger = logging.getLogger("config.settings")

BASE_DIR = Path(__file__).resolve().parents[2]  # JARVIS 프로젝트 루트
dotenv_path = find_dotenv(str(BASE_DIR / ".env"), raise_error_if_not_found=False)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    logger.warning("프로젝트 루트에서 .env 파일을 찾지 못했습니다. 환경 변수가 비어있을 수 있습니다.")

class Settings(BaseSettings):
    # API 설정
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # 데이터베이스 설정
    DATABASE_URL: str = f"sqlite:///{(BASE_DIR / 'jarvis.db').as_posix()}"

    # Gemini API 설정
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    # Multimodal RAG 설정
    IMAGE_UPLOAD_PATH: str = "./uploads/images"
    IMAGE_PROCESSING_SIZE: tuple = (448, 448)
    MAX_IMAGE_SIZE_MB: int = 10
    SUPPORTED_IMAGE_FORMATS: list = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
    
    # RAG 설정
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    VECTOR_DB_PATH: str = "./vector_db"

    # ColQwen2 설정
    COLQWEN2_BASE_URL: str = "http://localhost:11434"
    COLQWEN2_MODEL: str = "qwen2.5-72b-instruct"
    
    # 이메일 설정
    EMAIL_FROM: Optional[str] = None
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    EMAIL_USERNAME: Optional[str] = None
    EMAIL_PASSWORD: Optional[str] = None

    # 로깅 설정
    LOG_LEVEL: str = "DEBUG"  # DEBUG로 변경하여 더 자세한 로그 확인
    ENABLE_FILE_LOG: bool = False  # 기본적으로 파일 로깅 비활성화
    LOG_FILE_PATH: str = ""  # 파일 로깅을 사용할 경우 경로
    LOG_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5  # 백업 파일 개수
    
    # 서버 타임아웃 설정
    REQUEST_TIMEOUT: int = 120  # 요청 타임아웃 (초)
    KEEP_ALIVE_TIMEOUT: int = 5  # Keep-alive 타임아웃 (초)
    
    # Report Agent 설정
    REPORTS_DIR: str = str(Path.home() / "Documents" / "JARVIS" / "Reports")
    
    # Google OAuth 2.0 설정
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: str = "http://127.0.0.1:9090/auth/callback"
    
    # JWT 설정
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-this-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"  # 정의되지 않은 환경 변수 무시

settings = Settings()

if not settings.GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY가 설정되지 않았습니다. Gemini 기반 기능이 제한됩니다.")