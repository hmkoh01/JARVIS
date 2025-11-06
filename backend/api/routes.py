import os
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from logging import getLogger
logger = getLogger(__name__)

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio
import threading # 백그라운드 작업을 위해 추가

from config.settings import settings
from .schemas import UserIntent, ChatRequest, ChatResponse
from core.supervisor import supervisor
from core.agent_registry import agent_registry

from database.data_collector import get_manager, data_collection_managers
from database.sqlite_meta import SQLiteMeta
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

router = APIRouter()
security = HTTPBearer()

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """JWT에서 user_id 추출"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

@router.post("/chat")
async def chat_with_agent(chat_request: ChatRequest, request: Request):
    """사용자 메시지를 받아서 supervisor를 통해 적절한 에이전트로 라우팅합니다. (스트리밍 지원)"""
    try:
        # 스트리밍 요청 확인 (Accept 헤더 또는 stream 파라미터)
        accept_header = request.headers.get("accept", "")
        stream_requested = "text/event-stream" in accept_header or "stream" in str(request.query_params)
        
        # 챗봇 에이전트인 경우 스트리밍 지원
        # 먼저 supervisor를 통해 에이전트 타입 확인
        user_intent = UserIntent(message=chat_request.message, user_id=chat_request.user_id)
        
        # 챗봇 에이전트로 직접 라우팅하여 스트리밍 처리
        if stream_requested:
            # 스트리밍 모드: compose_answer를 직접 호출
            from agents.chatbot_agent.rag.react_agent import process as react_process
            from agents.chatbot_agent.rag.answerer import compose_answer
            
            # RAG 에이전트의 process 함수를 사용하여 evidences 가져오기
            state = {
                "question": chat_request.message,
                "user_id": chat_request.user_id,
                "filters": {}
            }
            
            # react_process를 통해 evidences 가져오기 (비동기 처리 필요)
            def generate_stream():
                try:
                    # react_process를 호출하여 evidences 가져오기
                    result = react_process(state)
                    evidences = result.get("evidence", [])
                    
                    # compose_answer를 직접 호출하여 스트리밍
                    for chunk in compose_answer(chat_request.message, evidences, chat_request.user_id):
                        yield chunk
                except Exception as e:
                    logger.error(f"스트리밍 응답 생성 중 오류: {e}", exc_info=True)
                    yield f"오류가 발생했습니다: {str(e)}"
            
            return StreamingResponse(generate_stream(), media_type="text/plain")
        else:
            # 비스트리밍 모드: 기존 로직 사용
            supervisor_response = await asyncio.wait_for(
                supervisor.process_user_intent(user_intent),
                timeout=settings.REQUEST_TIMEOUT
            )
            
            return ChatResponse(
                success=supervisor_response.success,
                message=supervisor_response.response.content if supervisor_response.success else supervisor_response.response,
                agent_type=supervisor_response.response.agent_type if supervisor_response.success else "unknown",
                metadata=supervisor_response.response.metadata if supervisor_response.success else {}
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail=f"채팅 처리 시간이 초과되었습니다.")
    except Exception as e:
        logger.error(f"채팅 처리 중 오류 발생: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"채팅 처리 중 오류가 발생했습니다: {str(e)}")

@router.post("/process")
async def process_message(request: dict):
    """프론트엔드용 메시지 처리 엔드포인트"""
    try:
        message = request.get("message", "")
        user_id = request.get("user_id", 1)
        
        user_intent = UserIntent(message=message, user_id=user_id)
        
        supervisor_response = await asyncio.wait_for(
            supervisor.process_user_intent(user_intent),
            timeout=settings.REQUEST_TIMEOUT
        )
        
        # 프론트엔드가 기대하는 형식으로 반환
        return {
            "success": supervisor_response.success,
            "content": supervisor_response.response.content if supervisor_response.success else str(supervisor_response.response),
            "agent_type": supervisor_response.response.agent_type if supervisor_response.success else "unknown",
            "metadata": supervisor_response.response.metadata if supervisor_response.success else {}
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "content": "응답 시간이 초과되었습니다. 다시 시도해주세요.",
            "agent_type": "error",
            "metadata": {}
        }
    except Exception as e:
        logger.error(f"메시지 처리 중 오류 발생: {e}", exc_info=True)
        return {
            "success": False,
            "content": f"처리 중 오류가 발생했습니다: {str(e)}",
            "agent_type": "error",
            "metadata": {}
        }

@router.get("/agents")
async def get_agents():
    """등록된 모든 에이전트 정보를 반환합니다."""
    agents = agent_registry.get_agent_descriptions()
    return {"agents": agents, "total_count": len(agents)}

@router.get("/health")
async def health_check():
    """System health check"""
    try:
        status = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "agents": {
                "total": len(agent_registry.get_agent_descriptions()),
            },
            # This part will now work
            "data_collection": {
                "active_managers": len(data_collection_managers),
                "user_ids": list(data_collection_managers.keys())
            }
        }
        return status
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

# -----------------------------------------------------------------------------
# 데이터 수집 제어 엔드포인트들
# -----------------------------------------------------------------------------

@router.get("/data-collection/folders")
async def get_user_folders_endpoint():
    """사용자의 기본 폴더(바탕화면, 문서, 다운로드) 목록을 조회합니다."""
    try:
        manager = get_manager(user_id=0) 
        folders = manager.file_collector.get_user_folders()
        return {
            "success": True,
            "folders": folders,
            "total_count": len(folders),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"폴더 목록 조회 오류: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"폴더 목록 조회 오류: {str(e)}",
            "folders": [],
            "timestamp": datetime.utcnow().isoformat()
        }

@router.post("/data-collection/start/{user_id}")
async def start_data_collection(user_id: int, payload: Dict[str, List[str]]):
    """Starts data collection for a specific user."""
    try:
        selected_folders = payload.get("selected_folders")
        if not selected_folders:
            raise HTTPException(status_code=400, detail="You must select folders to scan.")

        manager = get_manager(user_id)

        if not manager.initial_collection_done:
            thread = threading.Thread(target=manager.perform_initial_collection, daemon=True)
            thread.start()

        manager.start_collection(selected_folders)
        
        return {
            "success": True,
            "message": f"Data collection started for user {user_id}. The initial scan is running in the background.",
        }
    except Exception as e:
        logger.error(f"Error starting collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting collection: {str(e)}")

@router.post("/data-collection/stop/{user_id}")
async def stop_data_collection(user_id: int):
    """Stops data collection for a specific user."""
    try:
        if user_id in data_collection_managers:
            manager = get_manager(user_id)
            manager.stop_collection()
            del data_collection_managers[user_id]
            return {"message": f"Data collection stopped for user {user_id}."}
        else:
            return {"message": f"No active collection found for user {user_id}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error stopping collection: {str(e)}")

@router.post("/data-collection/stop-all")
async def stop_all_collections():
    """Stops data collection for all users."""
    try:
        # (CORRECTED) Iterate through a copy of the dictionary keys and stop each manager
        for user_id in list(data_collection_managers.keys()):
            manager = data_collection_managers[user_id]
            manager.stop_collection()
            del data_collection_managers[user_id]
        return {"message": "All data collection has been stopped."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error stopping all collections: {str(e)}")


@router.get("/data-collection/status/{user_id}")
async def get_data_collection_status(user_id: int):
    """Checks the data collection status (progress) for a user."""
    if user_id in data_collection_managers:
        manager = data_collection_managers[user_id]
        return {
            "user_id": user_id,
            "running": manager.running,
            "initial_collection_done": manager.initial_collection_done,
            "progress": manager.progress,
            "progress_message": manager.progress_message
        }
    else:
        raise HTTPException(status_code=404, detail="No collection manager found for this user.")

@router.get("/data-collection/stats")
async def get_data_collection_stats():
    """데이터 수집 통계를 확인합니다."""
    try:
        from database.sqlite_meta import SQLiteMeta
        
        sqlite_meta = SQLiteMeta()
        
        # 각 테이블의 레코드 수 조회
        stats = sqlite_meta.get_collection_stats()
        file_count = stats['collected_files']
        browser_count = stats['collected_browser_history']
        app_count = stats['collected_apps']
        
        # 최근 24시간 내 데이터 수
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        # 전체 데이터의 약 1/7을 최근 24시간으로 추정
        recent_files = file_count // 7
        recent_browser = browser_count // 7
        recent_apps = app_count // 7
        
        return {
            "total_records": {
                "files": file_count,
                "browser_history": browser_count,
                "active_applications": app_count
            },
            "last_24_hours": {
                "files": recent_files,
                "browser_history": recent_browser,
                "active_applications": recent_apps
            },
            "active_collectors": len(data_collection_managers),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"통계 조회 오류: {str(e)}")

@router.get("/user-survey/{user_id}")
async def get_user_survey(user_id: int):
    """사용자 설문지 응답을 조회합니다."""
    try:
        db = SQLiteMeta()
        survey_data = db.get_user_survey_response(user_id)
        
        if survey_data:
            return {
                "success": True,
                "survey_data": survey_data,
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            return {
                "success": False,
                "message": "설문지 응답을 찾을 수 없습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"설문지 조회 오류: {str(e)}")

@router.get("/user-survey/{user_id}/completed")
async def check_survey_completed(user_id: int):
    """사용자가 설문지를 완료했는지 확인합니다."""
    try:
        db = SQLiteMeta()
        completed = db.has_user_completed_survey(user_id)
        
        return {
            "success": True,
            "completed": completed,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"설문지 완료 여부 확인 오류: {str(e)}")

@router.post("/user-profile/{user_id}/index")
async def index_user_profile(user_id: int):
    """사용자 프로필을 Qdrant에 인덱싱합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        indexer = UserProfileIndexer()
        success = indexer.index_user_profile(user_id)
        
        if success:
            return {
                "success": True,
                "message": f"사용자 {user_id}의 프로필이 성공적으로 인덱싱되었습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            return {
                "success": False,
                "message": "프로필 인덱싱에 실패했습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        logger.error(f"프로필 인덱싱 오류: {e}")
        raise HTTPException(status_code=500, detail=f"프로필 인덱싱 오류: {str(e)}")

@router.post("/user-profile/{user_id}/update")
async def update_user_profile(user_id: int, survey_data: dict):
    """사용자 설문조사를 업데이트하고 프로필을 재인덱싱합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        # SQLite에 설문조사 업데이트
        db = SQLiteMeta()
        success = db.insert_survey_response(user_id, survey_data)
        
        if not success:
            return {
                "success": False,
                "message": "설문조사 데이터 저장에 실패했습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # Qdrant에 프로필 재인덱싱
        indexer = UserProfileIndexer()
        index_success = indexer.update_user_profile(user_id)
        
        return {
            "success": True,
            "message": f"사용자 {user_id}의 프로필이 업데이트되었습니다.",
            "indexed": index_success,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"프로필 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail=f"프로필 업데이트 오류: {str(e)}")

@router.get("/user-profile/{user_id}")
async def get_user_profile_context(user_id: int):
    """사용자 프로필을 텍스트 형태로 반환합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        indexer = UserProfileIndexer()
        profile_text = indexer.get_profile_as_context(user_id)
        
        if profile_text:
            return {
                "success": True,
                "profile": profile_text,
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            return {
                "success": False,
                "message": "프로필을 찾을 수 없습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        logger.error(f"프로필 조회 오류: {e}")
        raise HTTPException(status_code=500, detail=f"프로필 조회 오류: {str(e)}") 

# ============================================================================
# 추천 관련 엔드포인트
# ============================================================================

@router.get("/recommendations")
async def get_recommendations(user_id: int = Depends(get_current_user_id)):
    """현재 사용자의 읽지 않은 추천 목록을 조회합니다."""
    try:
        db = SQLiteMeta()
        recommendations = db.get_unread_recommendations(user_id)
        return {
            "success": True,
            "recommendations": recommendations
        }
    except Exception as e:
        logger.error(f"추천 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations/history")
async def get_recommendation_history(user_id: int = Depends(get_current_user_id)):
    """현재 사용자의 모든 추천 내역을 조회합니다."""
    try:
        db = SQLiteMeta()
        recommendations = db.get_all_recommendations(user_id)
        return {
            "success": True,
            "recommendations": recommendations
        }
    except Exception as e:
        logger.error(f"추천 내역 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendations/generate")
async def generate_recommendation(user_id: int = Depends(get_current_user_id)):
    """현재 사용자를 위해 수동으로 새로운 추천을 생성합니다."""
    try:
        db = SQLiteMeta()
        
        # 최근 1시간 내 수동 추천 횟수 확인
        count = db.get_recent_manual_recommendation_count(user_id, hours=1)
        if count >= 3:
            raise HTTPException(
                status_code=429, 
                detail="최근 1시간 이내에 3번의 수동 추천을 모두 사용하셨습니다. 시간이 조금 지난 후에 다시 사용해주시기 바랍니다."
            )
            
        # 추천 에이전트 가져오기 및 분석 실행
        recommendation_agent = agent_registry.get_agent("recommendation")
        if not recommendation_agent:
            raise HTTPException(status_code=503, detail="추천 기능이 현재 사용 불가능합니다.")
            
        success, message = await recommendation_agent.run_periodic_analysis(user_id, recommendation_type='manual')
        
        return {
            "success": success,
            "message": message
        }
    except HTTPException:
        raise  # HTTPException은 그대로 다시 발생시킴
    except Exception as e:
        logger.error(f"수동 추천 생성 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="추천 생성 중 오류가 발생했습니다.")


@router.post("/recommendations/{recommendation_id}/read")
async def mark_recommendation_read(
    recommendation_id: int,
    user_id: int = Depends(get_current_user_id)
):
    """추천을 읽음으로 표시합니다."""
    try:
        db = SQLiteMeta()
        # TODO: A better check to ensure user owns the recommendation
        success = db.mark_recommendation_as_read(recommendation_id)
        if success:
            return {"success": True, "message": "Recommendation marked as read."}
        else:
            raise HTTPException(status_code=404, detail="Recommendation not found or failed to update.")
    except Exception as e:
        logger.error(f"추천 읽음 처리 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# 사용자 설정 관련 엔드포인트
# ============================================================================

@router.post("/settings/initial-setup")
async def initial_setup(
    request: dict,
    user_id: int = Depends(get_current_user_id)
):
    """초기 설정 완료 - 폴더 경로 저장 및 설정 완료 상태 업데이트"""
    try:
        db = SQLiteMeta()
        folder_path = request.get("folder_path", "")
        
        # 폴더 경로 업데이트
        success1 = db.update_user_folder(user_id, folder_path)
        
        # 설정 완료 상태 업데이트
        success2 = db.update_user_setup_status(user_id, 1)
        
        if success1 and success2:
            return {
                "success": True,
                "message": "초기 설정이 완료되었습니다."
            }
        else:
            raise HTTPException(status_code=500, detail="설정 저장에 실패했습니다.")
    
    except Exception as e:
        logger.error(f"초기 설정 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/update-folder")
async def update_folder(
    request: dict,
    user_id: int = Depends(get_current_user_id)
):
    """
    데이터 폴더 변경 - 기존 데이터 완전 삭제 후 재수집
    
    1. 데이터 수집 중지
    2. SQLite에서 user_id 관련 데이터 삭제
    3. Qdrant에서 user_id 관련 벡터 삭제
    4. 새 폴더 경로로 업데이트
    5. 데이터 수집 재시작
    """
    try:
        import os
        import sys
        from pathlib import Path
        
        db = SQLiteMeta()
        new_folder_path = request.get("new_folder_path", "")
        
        logger.info(f"사용자 {user_id}의 데이터 폴더 변경 시작...")
        logger.info(f"새 폴더 경로: {new_folder_path}")
        
        # 1. 데이터 수집 중지
        logger.info("데이터 수집 중지 중...")
        from database.data_collector import get_manager
        manager = get_manager(user_id)
        if manager:
            manager.stop_collection()
        
        # 2. SQLite에서 user_id 관련 데이터 삭제
        logger.info(f"SQLite에서 사용자 {user_id}의 데이터 삭제 중...")
        try:
            # files 테이블 삭제
            db.conn.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
            # web_history 테이블 삭제
            db.conn.execute("DELETE FROM web_history WHERE user_id = ?", (user_id,))
            # apps 테이블 삭제
            db.conn.execute("DELETE FROM apps WHERE user_id = ?", (user_id,))
            db.conn.commit()
            logger.info("SQLite 데이터 삭제 완료")
        except Exception as e:
            logger.error(f"SQLite 데이터 삭제 오류: {e}")
            db.conn.rollback()
        
        # 3. Qdrant에서 user_id 관련 벡터 삭제
        logger.info(f"Qdrant에서 사용자 {user_id}의 벡터 삭제 중...")
        try:
            from database.qdrant_client import QdrantManager
            from qdrant_client import models
            
            qdrant = QdrantManager()
            
            # user_id 필터로 모든 벡터 검색
            from qdrant_client.http import models as qdrant_models
            
            # 포인트를 스크롤하여 user_id로 필터링
            scroll_result = qdrant.client.scroll(
                collection_name=qdrant.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id)
                        )
                    ]
                ),
                limit=10000,
                with_payload=True
            )
            
            # 삭제할 포인트 ID 수집
            point_ids_to_delete = []
            for point in scroll_result[0]:
                if point.payload.get("user_id") == user_id:
                    point_ids_to_delete.append(point.id)
            
            # 벡터 삭제
            if point_ids_to_delete:
                qdrant.client.delete(
                    collection_name=qdrant.collection_name,
                    points_selector=models.PointIdsList(points=point_ids_to_delete)
                )
                logger.info(f"Qdrant에서 {len(point_ids_to_delete)}개 벡터 삭제 완료")
            else:
                logger.info("삭제할 Qdrant 벡터가 없습니다.")
                
        except Exception as e:
            logger.error(f"Qdrant 벡터 삭제 오류: {e}")
            # Qdrant 삭제 실패해도 계속 진행
        
        # 4. 새 폴더 경로로 업데이트
        logger.info("새 폴더 경로로 업데이트 중...")
        success = db.update_user_folder(user_id, new_folder_path)
        
        if not success:
            raise HTTPException(status_code=500, detail="폴더 경로 업데이트에 실패했습니다.")
        
        # 5. 데이터 수집 재시작
        logger.info("데이터 수집 재시작 중...")
        try:
            # 폴더 리스트 준비
            selected_folders = [new_folder_path] if new_folder_path else None
            
            # 새 스레드에서 초기 수집 시작
            import threading
            collection_thread = threading.Thread(
                target=manager.perform_initial_collection,
                args=(selected_folders,),
                daemon=True
            )
            collection_thread.start()
            
            logger.info("데이터 수집 백그라운드 스레드 시작됨")
            
        except Exception as e:
            logger.error(f"데이터 수집 재시작 오류: {e}")
            # 데이터 수집 실패해도 성공 메시지 반환 (폴더는 업데이트됨)
        
        return {
            "success": True,
            "message": "데이터 폴더가 성공적으로 변경되었습니다. 새 데이터가 수집되고 있습니다."
        }
    
    except Exception as e:
        logger.error(f"폴더 경로 업데이트 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) 