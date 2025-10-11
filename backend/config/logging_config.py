import os
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime
from config.settings import settings

def setup_logging():
    """로깅 설정 초기화"""
    
    # 로그 디렉토리 생성
    log_dir = Path(settings.LOG_FILE_PATH).parent
    # 폴더가 존재하지 않을 때만 생성
    if not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)
    
    # 로그 레벨 설정
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # 로그 포맷 설정
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # 기존 핸들러 제거
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 파일 핸들러 설정 (로테이팅)
    file_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_FILE_PATH,
        maxBytes=settings.LOG_MAX_SIZE,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(log_format)
    
    # 콘솔 핸들러 설정
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(log_format)
    
    # 핸들러 추가
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 특정 로거들의 레벨 설정
    loggers_to_configure = [
        'uvicorn',
        'uvicorn.error',
        'uvicorn.access',
        'fastapi',
        'agents.chatbot_agent.rag.models.colqwen2_embedder',
        'agents.chatbot_agent.rag.retrievers',
        'agents.chatbot_agent.rag.react_agent',
        'database.repository',
        'database.qdrant_client',
        'core.supervisor'
    ]
    
    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
    
    # 시작 로그
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info("🚀 JARVIS Multi-Agent System 로깅 시스템 초기화 완료")
    logger.info(f"�� 로그 파일: {settings.LOG_FILE_PATH}")
    logger.info(f"�� 로그 레벨: {settings.LOG_LEVEL}")
    logger.info(f"⏰ 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

def get_logger(name: str) -> logging.Logger:
    """로거 인스턴스 반환"""
    return logging.getLogger(name)
