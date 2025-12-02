import logging
import logging.handlers
from pathlib import Path
from datetime import datetime
from config.settings import settings

def setup_logging():
    """로깅 설정 초기화 (EXE 환경 호환)"""
    
    # 로그 레벨 설정
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # 로그 포맷 설정
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 루트 로거 설정
    root_logger = logging.getLogger()
    
    # 이미 핸들러가 설정되어 있으면 중복 설정 방지
    if root_logger.handlers:
        return
    
    root_logger.setLevel(log_level)
    
    # 기존 핸들러 제거 (혹시 모를 경우 대비)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 파일 핸들러 설정 (로테이팅) - LOG_FILE_PATH가 비어있으면 콘솔만 사용
    handlers = []
    file_handler_error = None

    log_file_path = None
    if getattr(settings, "ENABLE_FILE_LOG", False) and settings.LOG_FILE_PATH:  # 파일 로깅이 활성화된 경우에만 파일 핸들러 생성
        try:
            configured_path = Path(settings.LOG_FILE_PATH)
            if not configured_path.is_absolute():
                configured_path = Path.cwd() / configured_path
            log_file_path = configured_path.resolve()
            
            # 상위 디렉토리가 없으면 생성
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_file_path),
                maxBytes=settings.LOG_MAX_SIZE,
                backupCount=settings.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(log_format)
            handlers.append(file_handler)
        except Exception as e:
            file_handler_error = e
    
    # 콘솔 핸들러는 항상 추가
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(log_format)
    handlers.append(console_handler)

    for handler in handlers:
        root_logger.addHandler(handler)
    
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
    
    # 외부 라이브러리 로거 억제 (불필요한 경고/디버그 메시지 숨김)
    noisy_loggers = [
        'trafilatura',           # "discarding data: None" 경고 억제
        'trafilatura.core',
        'trafilatura.utils',
        'courlan',               # trafilatura 관련
        'htmldate',              # trafilatura 관련
        'justext',               # trafilatura 관련
        'httpx',                 # HTTP 클라이언트 로그
        'httpcore',              # HTTP 코어 로그
        'charset_normalizer',    # 인코딩 관련 로그
        'PIL',                   # 이미지 처리 로그
    ]
    
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.ERROR)
    
    # 시작 로그
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info("🚀 JARVIS Multi-Agent System 로깅 시스템 초기화 완료")
    if getattr(settings, "ENABLE_FILE_LOG", False) and settings.LOG_FILE_PATH:
        logger.info(f"📁 로그 파일: {log_file_path if not file_handler_error else '비활성화'}")
    else:
            logger.info("📁 로그 파일: 콘솔 전용 모드")
    if file_handler_error:
        logger.warning(f"파일 로거를 사용할 수 없어 콘솔 로깅만 활성화되었습니다: {file_handler_error}")
    logger.info(f"📊 로그 레벨: {settings.LOG_LEVEL}")
    logger.info(f"⏰ 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

def get_logger(name: str) -> logging.Logger:
    """로거 인스턴스 반환"""
    return logging.getLogger(name)
