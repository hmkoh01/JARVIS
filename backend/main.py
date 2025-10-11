import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# 현재 스크립트의 디렉토리를 Python 경로에 추가
current_dir = Path(__file__).parent.absolute()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router
from core.agent_registry import agent_registry
from database.sqlite_meta import SQLiteMeta
from config.settings import settings
from config.logging_config import setup_logging, get_logger

# 로깅 설정 초기화
setup_logging()
logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Lifespan 이벤트 핸들러 (FastAPI 최신 방식)
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 애플리케이션 시작 시 실행될 코드 ---
    logger.info("🚀 JARVIS Multi-Agent System 시작")
    try:
        SQLiteMeta()
        logger.info("✅ SQLite 데이터베이스 초기화 완료")
    except Exception as e:
        logger.error(f"⚠️ 데이터베이스 초기화 오류: {e}")
    
    logger.info(f"📊 등록된 에이전트: {list(agent_registry.get_agent_descriptions().keys())}")
    logger.info("✅ 시스템이 준비되었습니다!")
    
    yield  # 이 시점에서 애플리케이션이 실행됨

    # --- 애플리케이션 종료 시 실행될 코드 ---
    logger.info("🛑 JARVIS Multi-Agent System 종료")
    try:
        from database.data_collector import data_collection_managers
        logger.info("모든 데이터 수집 관리자 중지 시도...")
        
        # 딕셔너리를 순회하면서 안전하게 중지 및 삭제하기 위해 키 리스트 복사
        for user_id in list(data_collection_managers.keys()):
            manager = data_collection_managers[user_id]
            manager.stop_collection()
            del data_collection_managers[user_id]
        logger.info("✅ 모든 데이터 수집 중지 완료")

    except Exception as e:
        logger.error(f"⚠️ 데이터 수집 중지 중 오류 발생: {e}")


# -----------------------------------------------------------------------------
# FastAPI 앱 설정
# -----------------------------------------------------------------------------
app = FastAPI(
    title="JARVIS Multi-Agent System",
    description="LangGraph 기반의 다중 에이전트 시스템",
    version="3.0.0",
    lifespan=lifespan  # on_event 대신 lifespan 사용
)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록
app.include_router(router, prefix="/api/v2")


# -----------------------------------------------------------------------------
# 기본 엔드포인트
# -----------------------------------------------------------------------------
@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "message": "JARVIS Multi-Agent System",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
    }


# -----------------------------------------------------------------------------
# 서버 실행
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info(f"서버 시작: {settings.API_HOST}:{settings.API_PORT}")
    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )