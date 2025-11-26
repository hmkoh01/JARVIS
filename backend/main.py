import os
import sys
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

# 현재 스크립트의 디렉토리를 Python 경로에 추가
current_dir = Path(__file__).parent.absolute()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from api.routes import router
from api.auth_routes import router as auth_router
from core.agent_registry import agent_registry
from database.sqlite import SQLite
from config.settings import settings
from config.logging_config import setup_logging, get_logger

# 로깅 설정 초기화
setup_logging()
logger = get_logger(__name__)

# --- ⬇️ 싱글톤 객체 임포트 및 전역 변수 선언 ⬇️ ---
from agents.chatbot_agent.rag.react_agent import ReactAgent
from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder
from database.repository import Repository
from database.user_profile_indexer import UserProfileIndexer

# 전역 싱글톤 인스턴스
global_react_agent: ReactAgent = None
global_embedder: BGEM3Embedder = None
global_repository: Repository = None
global_profile_indexer: UserProfileIndexer = None


# 전역 스케줄러 인스턴스
scheduler = AsyncIOScheduler()

async def trigger_recommendation_analysis():
    """
    주기적으로 추천 분석을 트리거하는 함수.
    모든 사용자에 대해 분석을 실행합니다.
    """
    logger.info("📈 주기적 추천 분석 시작...")
    try:
        # agent_registry에서 recommendation 에이전트를 가져옵니다.
        recommendation_agent = agent_registry.get_agent("recommendation")
        if recommendation_agent and hasattr(recommendation_agent, 'run_active_analysis'):
            # 모든 사용자에 대해 분석 실행
            db = SQLite()
            all_users = db.get_all_users()
            if not all_users:
                logger.info("분석할 사용자가 없습니다.")
                return

            logger.info(f"{len(all_users)}명의 사용자에 대한 분석을 시작합니다.")
            for user in all_users:
                user_id = user['user_id']
                await recommendation_agent.run_active_analysis(user_id)
        else:
            logger.warning("Recommendation agent 또는 분석 메서드를 찾을 수 없습니다.")

    except Exception as e:
        logger.error(f"주기적 추천 분석 중 오류 발생: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# Lifespan 이벤트 핸들러 (FastAPI 최신 방식)
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 애플리케이션 시작 시 실행될 코드 ---
    global global_react_agent, global_embedder, global_repository
    
    logger.info("🚀 JARVIS Multi-Agent System 시작")
    
    # 1. SQLite 데이터베이스 초기화
    try:
        SQLite()
        logger.info("✅ SQLite 데이터베이스 초기화 완료")
    except Exception as e:
        logger.error(f"⚠️ 데이터베이스 초기화 오류: {e}")
    
    # 2. 싱글톤 리소스 초기화 (BGE-M3 모델, Repository, ReactAgent)
    logger.info("--- Application Starting: Initializing Singleton Resources ---")
    try:
        CONFIG_PATH = "configs.yaml"
        
        # 2-1. BGEM3Embedder 초기화 (BGE-M3 모델 로드 - 약 4초 소요)
        logger.info("📦 BGE-M3 임베더 초기화 시작...")
        global_embedder = BGEM3Embedder(config_path=CONFIG_PATH)
        logger.info("✅ BGE-M3 임베더 초기화 완료")
        
        # 2-2. Repository 초기화
        logger.info("📦 Repository 초기화 시작...")
        global_repository = Repository(config_path=CONFIG_PATH)
        logger.info("✅ Repository 초기화 완료")
        
        # 2-3. ReactAgent 초기화 (의존성 주입)
        logger.info("📦 ReactAgent 초기화 시작...")
        global_react_agent = ReactAgent(
            repository=global_repository,
            embedder=global_embedder,
            config_path=CONFIG_PATH
        )
        logger.info("✅ ReactAgent 초기화 완료")
        
        # 2-4. 전역 싱글톤 인스턴스 설정 (react_agent.py의 함수 기반 래퍼에서 사용)
        from agents.chatbot_agent.rag.react_agent import set_global_react_agent
        set_global_react_agent(global_react_agent)
        logger.info("✅ 전역 ReactAgent 싱글톤 설정 완료")
        
        # 2-5. UserProfileIndexer 초기화 (의존성 주입)
        logger.info("📦 UserProfileIndexer 초기화 시작...")
        global_profile_indexer = UserProfileIndexer(
            repository=global_repository,
            embedder=global_embedder
        )
        from database.user_profile_indexer import set_global_profile_indexer
        set_global_profile_indexer(global_profile_indexer)
        logger.info("✅ UserProfileIndexer 초기화 완료")
        
        logger.info("--- ✅ Singleton Resources Initialized Successfully ---")
        
    except Exception as e:
        logger.error(f"❌ 싱글톤 리소스 초기화 실패: {e}", exc_info=True)
        # 실제 운영 시에는 여기서 앱을 종료시킬 수도 있음
        global_react_agent = None
        global_embedder = None
        global_repository = None
    
    # 3. 스케줄러 작업 추가 및 시작
    # 10분 간격으로 반복 실행 (실시간성 확보)
    scheduler.add_job(
        trigger_recommendation_analysis, 
        'interval', 
        minutes=10, 
        id='recommendation_analysis_job'
    )
    scheduler.start()
    logger.info("📅 주기적 추천 분석 스케줄러 시작됨 (10분 간격)")
    
    # 4. 서버 시작 시 즉시 1회 실행 (개발/테스트 편의성)
    asyncio.create_task(trigger_recommendation_analysis())
    logger.info("🚀 서버 시작 시 추천 분석 즉시 실행 트리거됨")

    logger.info(f"📊 등록된 에이전트: {list(agent_registry.get_agent_descriptions().keys())}")
    logger.info("✅ 시스템이 준비되었습니다!")
    
    # 5. app.state에 전역 인스턴스 저장 (라우터에서 접근 가능하도록)
    app.state.repository = global_repository
    app.state.embedder = global_embedder
    app.state.react_agent = global_react_agent
    app.state.profile_indexer = global_profile_indexer
    
    yield  # 이 시점에서 애플리케이션이 실행됨

    # --- 애플리케이션 종료 시 실행될 코드 ---
    logger.info("🛑 JARVIS Multi-Agent System 종료")
    
    # 스케줄러 종료
    if scheduler.running:
        scheduler.shutdown()
        logger.info("📅 스케줄러 종료됨")

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
app.include_router(auth_router)  # 인증 라우터 (prefix는 auth_routes.py에서 설정됨)


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