import os
import sys
import asyncio
from pathlib import Path
from typing import Tuple
from contextlib import asynccontextmanager

# 현재 스크립트의 디렉토리를 Python 경로에 추가
current_dir = Path(__file__).parent.absolute()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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


def _initialize_singletons(config_path: str) -> Tuple[BGEM3Embedder, Repository, ReactAgent, UserProfileIndexer]:
    """임베더/레포지토리 관련 싱글톤 의존성을 초기화한다."""
    embedder = BGEM3Embedder(config_path=config_path)
    repository = Repository(config_path=config_path)
    react_agent = ReactAgent(
        repository=repository,
        embedder=embedder,
        config_path=config_path
    )
    profile_indexer = UserProfileIndexer(
        repository=repository,
        embedder=embedder
    )
    from agents.chatbot_agent.rag.react_agent import set_global_react_agent
    from database.user_profile_indexer import set_global_profile_indexer
    set_global_react_agent(react_agent)
    set_global_profile_indexer(profile_indexer)
    return embedder, repository, react_agent, profile_indexer

async def trigger_recommendation_analysis(force_recommend: bool = False):
    """
    주기적으로 추천 분석을 트리거하는 함수.
    모든 사용자에 대해 분석을 실행합니다.
    
    Args:
        force_recommend: True면 데이터가 있을 경우 무조건 추천 생성 (시작 시 초기 분석용)
    """
    from database.data_collector import data_collection_managers
    
    logger.info(f"📈 추천 분석 시작... (force_recommend={force_recommend})")
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
                
                # 초기 데이터 수집이 완료되지 않은 사용자는 스킵
                if user_id in data_collection_managers:
                    manager = data_collection_managers[user_id]
                    if not manager.initial_collection_done:
                        logger.info(f"⏸️ 사용자 {user_id}의 초기 데이터 수집이 진행 중입니다. 추천 분석을 스킵합니다.")
                        continue
                
                logger.info(f"🔍 사용자 {user_id} 추천 분석 시작...")
                success, message = await recommendation_agent.run_active_analysis(user_id, force_recommend=force_recommend)
                if success:
                    logger.info(f"✅ 사용자 {user_id} 추천 분석 완료: {message}")
                else:
                    logger.info(f"ℹ️ 사용자 {user_id} 추천 미생성: {message}")
        else:
            logger.warning("Recommendation agent 또는 분석 메서드를 찾을 수 없습니다.")

    except Exception as e:
        logger.error(f"추천 분석 중 오류 발생: {e}", exc_info=True)


async def trigger_recommendation_for_user(user_id: int):
    """
    특정 사용자에 대해 추천 분석을 트리거합니다.
    사용자가 앱에 접속(WebSocket 연결)할 때 호출되어 새 추천을 생성합니다.
    
    Args:
        user_id: 사용자 ID
    """
    from database.data_collector import data_collection_managers
    
    logger.info(f"🎯 사용자 {user_id} 접속 - 새 추천 생성 시작 (force_recommend=True)")
    try:
        recommendation_agent = agent_registry.get_agent("recommendation")
        if recommendation_agent and hasattr(recommendation_agent, 'run_active_analysis'):
            # 초기 데이터 수집이 완료되지 않은 사용자는 스킵
            if user_id in data_collection_managers:
                manager = data_collection_managers[user_id]
                if not manager.initial_collection_done:
                    logger.info(f"⏸️ 사용자 {user_id}의 초기 데이터 수집이 진행 중입니다. 추천 생성을 스킵합니다.")
                    return
            
            # force_recommend=True로 무조건 새 추천 생성
            success, message = await recommendation_agent.run_active_analysis(user_id, force_recommend=True)
            if success:
                logger.info(f"✅ 사용자 {user_id} 접속 시 새 추천 생성 완료: {message}")
            else:
                logger.info(f"ℹ️ 사용자 {user_id} 접속 시 추천 생성 실패: {message}")
        else:
            logger.warning("Recommendation agent 또는 분석 메서드를 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"사용자 {user_id} 추천 생성 중 오류: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# Lifespan 이벤트 핸들러 (FastAPI 최신 방식)
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 애플리케이션 시작 시 실행될 코드 ---
    global global_react_agent, global_embedder, global_repository, global_profile_indexer
    global_react_agent = None
    global_embedder = None
    global_repository = None
    global_profile_indexer = None
    
    logger.info("🚀 JARVIS Multi-Agent System 시작")
    
    # 1. SQLite 데이터베이스 초기화 및 마이그레이션
    try:
        db = SQLite()
        logger.info("✅ SQLite 마스터 데이터베이스 초기화 완료")
        
        # 1-1. 기존 사용자 DB 파일들에 대해 마이그레이션 실행
        logger.info("📦 기존 사용자 DB 마이그레이션 시작...")
        migration_result = db.migrate_all_user_dbs()
        logger.info(
            f"✅ DB 마이그레이션 완료: "
            f"총 {migration_result['total']}개, "
            f"성공 {migration_result['success']}개, "
            f"실패 {migration_result['failed']}개"
        )
        if migration_result['errors']:
            for error in migration_result['errors']:
                logger.warning(f"  - {error}")
                
    except Exception as e:
        logger.error(f"⚠️ 데이터베이스 초기화 오류: {e}")
    
    # 2. 싱글톤 리소스 초기화 (BGE-M3 모델, Repository, ReactAgent)
    logger.info("--- Application Starting: Initializing Singleton Resources ---")
    CONFIG_PATH = "configs.yaml"
    try:
        logger.info("📦 싱글톤 리소스 초기화 시작...")
        embedder, repository, react_agent, profile_indexer = _initialize_singletons(CONFIG_PATH)
        
        # 전역 변수에 할당
        global_embedder = embedder
        global_repository = repository
        global_react_agent = react_agent
        global_profile_indexer = profile_indexer
        
        logger.info("--- ✅ Singleton Resources Initialized Successfully ---")
        
    except Exception as e:
        logger.error(f"❌ 싱글톤 리소스 초기화 실패: {e}", exc_info=True)
        # 실제 운영 시에는 여기서 앱을 종료시킬 수도 있음
        global_react_agent = None
        global_embedder = None
        global_repository = None
        global_profile_indexer = None
    
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
    
    # 4. 서버 시작 시 즉시 1회 실행 (force_recommend=True로 무조건 추천 생성)
    asyncio.create_task(trigger_recommendation_analysis(force_recommend=True))
    logger.info("🚀 서버 시작 시 초기 추천 분석 즉시 실행 트리거됨 (force_recommend=True)")

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
# WebSocket 엔드포인트 (실시간 알림용)
# -----------------------------------------------------------------------------
from core.websocket_manager import get_websocket_manager
from jose import jwt, JWTError

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    """
    WebSocket 연결 엔드포인트
    토큰으로 사용자 인증 후 연결을 유지하고 실시간 알림을 전송합니다.
    """
    ws_manager = get_websocket_manager()
    user_id = None
    
    try:
        # JWT 토큰에서 user_id 추출
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("user_id")
        
        if not user_id:
            await websocket.close(code=4001, reason="Invalid token")
            return
        
        # WebSocket 연결 수락
        await ws_manager.connect(websocket, user_id)
        
        # 연결 시 대기 중인 추천이 있으면 첫 번째 하나만 전송
        db = SQLite()
        pending_recommendations = db.get_pending_recommendations(user_id)
        if pending_recommendations:
            # 첫 번째 추천만 전송 (한 번에 하나씩만 표시)
            await ws_manager.broadcast_recommendation(user_id, pending_recommendations[0])
        else:
            # 대기 중인 추천이 없을 때만 새 추천 생성 (백그라운드에서 실행)
            asyncio.create_task(trigger_recommendation_for_user(user_id))
        
        # 연결 유지 (클라이언트로부터 메시지 대기)
        while True:
            try:
                # 클라이언트로부터 ping/pong 또는 메시지 수신 대기
                data = await websocket.receive_text()
                
                # ping 메시지에 pong으로 응답
                if data == "ping":
                    await websocket.send_text("pong")
                    
            except WebSocketDisconnect:
                break
                
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return
    except Exception as e:
        logger.error(f"WebSocket 오류: {e}", exc_info=True)
    finally:
        if user_id:
            ws_manager.disconnect(websocket, user_id)


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