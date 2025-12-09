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

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any, Optional
import tempfile
import hashlib
from datetime import datetime
import asyncio
import threading # 백그라운드 작업을 위해 추가

from config.settings import settings
from .schemas import UserIntent, ChatRequest, ChatResponse, MessageRequest, MessageResponse
from core.supervisor import get_supervisor, UserIntent as SupervisorUserIntent
from core.agent_registry import agent_registry

from database.data_collector import get_manager, data_collection_managers
from database.repository import Repository
from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder
from database.sqlite import SQLite
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

# KeywordExtractor 싱글톤 (채팅 키워드 추출용)
try:
    from utils.keyword_extractor import get_keyword_extractor
    _keyword_extractor = get_keyword_extractor()
except Exception as e:
    logger.warning(f"KeywordExtractor 초기화 실패, 키워드 추출 비활성화: {e}")
    _keyword_extractor = None

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

# =============================================================================
# 통합 메시지 엔드포인트 (권장)
# =============================================================================

@router.post("/message")
async def unified_message(message_request: MessageRequest, request: Request):
    """
    통합 메시지 처리 엔드포인트 (멀티에이전트 스트리밍 지원)
    
    모든 채팅 요청이 이 엔드포인트를 통해 Supervisor로 라우팅됩니다.
    - 의도 분석 (LLM 기반)
    - 에이전트 선택 및 스케줄링
    - 단계별 스트리밍 응답 (실행 계획 → 에이전트 실행 → 결과)
    
    스트리밍 응답 형식:
    - ---PLAN---: 실행 계획 (JSON)
    - ---START---: 에이전트 실행 시작
    - ---RESULT---: 에이전트 결과
    - ---ERROR---: 에이전트 오류
    - ---COMPLETE---: 전체 완료
    - ---METADATA---: 메타데이터 (버튼 표시용)
    """
    import json as json_module
    
    try:
        message = message_request.message
        user_id = message_request.user_id
        stream_requested = message_request.stream
        
        # Accept 헤더로 스트리밍 요청 확인 (오버라이드)
        accept_header = request.headers.get("accept", "")
        if "text/event-stream" in accept_header:
            stream_requested = True
        
        # 채팅 메시지 로깅 (사용자 메시지)
        db = SQLite()
        user_message_id = -1
        if message.strip():
            user_message_id = db.log_chat_message(
                user_id=user_id,
                role='user',
                content=message,
                metadata={"type": "user"}
            )
            # 백그라운드에서 키워드 추출 (10자 이상인 경우만)
            if user_message_id > 0 and len(message.strip()) >= 10:
                asyncio.create_task(_extract_and_store_keywords(user_id, message, user_message_id))
        
        # Supervisor를 통한 처리
        supervisor_instance = get_supervisor()
        user_intent = SupervisorUserIntent(message=message, user_id=user_id)
        
        # 스트리밍 모드 - 멀티에이전트 단계별 스트리밍
        if stream_requested:
            async def generate_multi_agent_stream():
                """멀티에이전트 단계별 스트리밍 응답 생성"""
                full_content_parts = []
                final_metadata = {}
                
                try:
                    async for event in supervisor_instance.process_user_intent_streaming(user_intent):
                        event_type = event.get("type", "")
                        
                        if event_type == "analyzing":
                            # 의도 분석 시작 - 프런트에서 로딩 상태 업데이트용
                            message = event.get("message", "의도를 분석하고 있어요...")
                            yield f"---ANALYZING---\n{message}\n\n"
                        
                        elif event_type == "analyzed":
                            # 의도 분석 완료 - 서론 텍스트 먼저 전송
                            intro_text = event.get("intro_text", "")
                            if intro_text:
                                yield intro_text + "\n\n"
                            
                            analyzed_data = {
                                "agents": event.get("agents", []),
                                "agent_count": event.get("agent_count", 1)
                            }
                            yield f"---ANALYZED---\n{json_module.dumps(analyzed_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "plan":
                            # 실행 계획 전송
                            plan_data = {
                                "agents": event.get("agents", []),
                                "sub_tasks": event.get("sub_tasks", {}),
                                "execution_mode": event.get("execution_mode", "sequential"),
                                "confidence": event.get("confidence", 0.8)
                            }
                            yield f"---PLAN---\n{json_module.dumps(plan_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "start":
                            # 에이전트 실행 시작
                            start_data = {
                                "agent": event.get("agent", ""),
                                "order": event.get("order", 0),
                                "total": event.get("total", 0),
                                "task": event.get("task", ""),
                                "focus": event.get("focus", "")
                            }
                            yield f"---START---\n{json_module.dumps(start_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "result":
                            # 에이전트 결과
                            agent_type = event.get("agent", "")
                            content = event.get("content", "")
                            success = event.get("success", True)
                            metadata = event.get("metadata", {})
                            
                            result_data = {
                                "agent": agent_type,
                                "order": event.get("order", 0),
                                "success": success,
                                "elapsed_time": event.get("elapsed_time", 0),
                                "metadata": metadata
                            }
                            yield f"---RESULT---\n{json_module.dumps(result_data, ensure_ascii=False)}\n"
                            
                            # 실제 내용 스트리밍 (청크 단위)
                            if content:
                                chunk_size = 80
                                for i in range(0, len(content), chunk_size):
                                    chunk = content[i:i + chunk_size]
                                    yield chunk
                                    await asyncio.sleep(0.01)
                                yield "\n\n"
                                
                                full_content_parts.append(content)
                            
                            # 메타데이터 병합 (action이 있는 것 우선)
                            if metadata:
                                if metadata.get("action"):
                                    final_metadata = metadata
                                elif not final_metadata.get("action"):
                                    final_metadata.update(metadata)
                        
                        elif event_type == "error":
                            # 에이전트 오류 (계속 진행)
                            error_data = {
                                "agent": event.get("agent", ""),
                                "order": event.get("order", 0),
                                "error": event.get("error", "알 수 없는 오류")
                            }
                            yield f"---ERROR---\n{json_module.dumps(error_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "cancelled":
                            # 취소됨
                            cancel_data = {
                                "completed": event.get("completed", []),
                                "remaining": event.get("remaining", [])
                            }
                            yield f"---CANCELLED---\n{json_module.dumps(cancel_data, ensure_ascii=False)}\n\n"
                            break
                        
                        elif event_type == "waiting_confirmation":
                            # 확인 대기
                            waiting_data = {
                                "agent": event.get("agent", ""),
                                "remaining_agents": event.get("remaining_agents", []),
                                "metadata": event.get("metadata", {})
                            }
                            yield f"---WAITING_CONFIRMATION---\n{json_module.dumps(waiting_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "complete":
                            # 전체 완료
                            complete_data = {
                                "total_agents": event.get("total_agents", 0),
                                "successful": event.get("successful", 0),
                                "failed": event.get("failed", 0),
                                "total_time": event.get("total_time", 0),
                                "waiting_confirmation": event.get("waiting_confirmation", False),
                                "remaining_agents": event.get("remaining_agents", [])
                            }
                            yield f"---COMPLETE---\n{json_module.dumps(complete_data, ensure_ascii=False)}\n\n"
                        
                        elif event_type == "fatal_error":
                            # 치명적 오류
                            yield f"---FATAL_ERROR---\n{event.get('error', '알 수 없는 오류')}\n\n"
                            break
                    
                    # 스트리밍 완료 후 로깅
                    full_content = "\n\n".join(full_content_parts)
                    if len(full_content.strip()) >= 10:
                        db.log_chat_message(
                            user_id=user_id,
                            role='assistant',
                            content=full_content,
                            metadata={"multi_agent": True, "streaming": True}
                        )
                    
                    # 메타데이터 전송 (버튼 표시용)
                    action = final_metadata.get("action", "")
                    if action in ("open_file", "confirm_report", "request_topic", "confirm_analysis"):
                        metadata_json = json_module.dumps(final_metadata, ensure_ascii=False)
                        yield f"---METADATA---\n{metadata_json}\n"
                        logger.info(f"[MAS] 메타데이터 전송: action={action}")
                        
                except Exception as e:
                    logger.error(f"멀티에이전트 스트리밍 중 오류: {e}", exc_info=True)
                    yield f"---FATAL_ERROR---\n{str(e)}\n"
            
            return StreamingResponse(generate_multi_agent_stream(), media_type="text/plain")
        
        # 비스트리밍 모드
        else:
            supervisor_response = await asyncio.wait_for(
                supervisor_instance.process_user_intent(user_intent, stream=False),
                timeout=settings.REQUEST_TIMEOUT
            )
            
            content = supervisor_response.response.content or ""
            agent_type = supervisor_response.response.agent_type
            
            # Assistant 응답 로깅
            if len(content.strip()) >= 10:
                db.log_chat_message(
                    user_id=user_id,
                    role='assistant',
                    content=content,
                    metadata={"agent_type": agent_type, "success": supervisor_response.success}
                )
            
            return MessageResponse(
                success=supervisor_response.success,
                content=content,
                agent_type=agent_type,
                metadata=supervisor_response.response.metadata or {}
            )
            
    except asyncio.TimeoutError:
        logger.error("메시지 처리 시간 초과")
        return MessageResponse(
            success=False,
            content="응답 시간이 초과되었습니다. 다시 시도해주세요.",
            agent_type="error",
            metadata={}
        )
    except Exception as e:
        logger.error(f"메시지 처리 중 오류 발생: {e}", exc_info=True)
        return MessageResponse(
            success=False,
            content=f"처리 중 오류가 발생했습니다: {str(e)}",
            agent_type="error",
            metadata={}
        )


# =============================================================================
# 남은 에이전트 실행 엔드포인트 (멀티에이전트 continuation)
# =============================================================================

@router.post("/continue-agents")
async def continue_agents(request_data: dict, request: Request):
    """
    남은 에이전트들을 실행합니다.
    
    이전 에이전트(예: report)가 확인을 받은 후 호출되어
    남은 에이전트들(예: coding)을 실행합니다.
    
    요청 JSON:
        {
            "message": "원본 사용자 메시지",
            "user_id": 1,
            "remaining_agents": ["coding"],
            "sub_tasks": {
                "coding": {"task": "...", "focus": "..."}
            },
            "previous_results": [...]
        }
    
    스트리밍 응답 형식 (멀티에이전트와 동일):
        ---PLAN---, ---START---, ---RESULT---, ---COMPLETE--- 등
    """
    import json as json_module
    
    try:
        message = request_data.get("message", "")
        user_id = request_data.get("user_id", 1)
        remaining_agents = request_data.get("remaining_agents", [])
        sub_tasks = request_data.get("sub_tasks", {})
        previous_results = request_data.get("previous_results", [])
        
        if not remaining_agents:
            return {
                "success": False,
                "content": "실행할 에이전트가 없습니다.",
                "agent_type": "error",
                "metadata": {}
            }
        
        logger.info(f"[MAS-CONTINUE] 남은 에이전트 실행 요청: {remaining_agents}")
        
        # Supervisor를 통한 처리
        supervisor_instance = get_supervisor()
        user_intent = SupervisorUserIntent(message=message, user_id=user_id)
        
        # 항상 스트리밍 모드로 응답
        async def generate_continuation_stream():
            """남은 에이전트 스트리밍 응답 생성"""
            full_content_parts = []
            final_metadata = {}
            
            try:
                async for event in supervisor_instance.process_remaining_agents_streaming(
                    user_intent, remaining_agents, sub_tasks, previous_results
                ):
                    event_type = event.get("type", "")
                    
                    if event_type == "plan":
                        plan_data = {
                            "agents": event.get("agents", []),
                            "sub_tasks": event.get("sub_tasks", {}),
                            "execution_mode": "sequential",
                            "is_continuation": event.get("is_continuation", True)
                        }
                        yield f"---PLAN---\n{json_module.dumps(plan_data, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "start":
                        start_data = {
                            "agent": event.get("agent", ""),
                            "order": event.get("order", 0),
                            "total": event.get("total", 0),
                            "task": event.get("task", ""),
                            "focus": event.get("focus", "")
                        }
                        yield f"---START---\n{json_module.dumps(start_data, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "result":
                        agent_type = event.get("agent", "")
                        content = event.get("content", "")
                        success = event.get("success", True)
                        metadata = event.get("metadata", {})
                        
                        result_data = {
                            "agent": agent_type,
                            "order": event.get("order", 0),
                            "success": success,
                            "elapsed_time": event.get("elapsed_time", 0),
                            "metadata": metadata
                        }
                        yield f"---RESULT---\n{json_module.dumps(result_data, ensure_ascii=False)}\n"
                        
                        # 실제 내용 스트리밍 (청크 단위)
                        if content:
                            chunk_size = 80
                            for i in range(0, len(content), chunk_size):
                                chunk = content[i:i + chunk_size]
                                yield chunk
                                await asyncio.sleep(0.01)
                            yield "\n\n"
                            
                            full_content_parts.append(content)
                        
                        # 메타데이터 병합
                        if metadata:
                            if metadata.get("action"):
                                final_metadata = metadata
                            elif not final_metadata.get("action"):
                                final_metadata.update(metadata)
                    
                    elif event_type == "error":
                        error_data = {
                            "agent": event.get("agent", ""),
                            "order": event.get("order", 0),
                            "error": event.get("error", "알 수 없는 오류")
                        }
                        yield f"---ERROR---\n{json_module.dumps(error_data, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "complete":
                        complete_data = {
                            "total_agents": event.get("total_agents", 0),
                            "successful": event.get("successful", 0),
                            "failed": event.get("failed", 0),
                            "total_time": event.get("total_time", 0),
                            "is_continuation": event.get("is_continuation", True)
                        }
                        yield f"---COMPLETE---\n{json_module.dumps(complete_data, ensure_ascii=False)}\n\n"
                    
                    elif event_type == "fatal_error":
                        yield f"---FATAL_ERROR---\n{event.get('error', '알 수 없는 오류')}\n\n"
                        break
                
                # 메타데이터 전송 (버튼 표시용)
                action = final_metadata.get("action", "")
                if action in ("open_file", "confirm_report", "request_topic", "confirm_analysis"):
                    metadata_json = json_module.dumps(final_metadata, ensure_ascii=False)
                    yield f"---METADATA---\n{metadata_json}\n"
                    logger.info(f"[MAS-CONTINUE] 메타데이터 전송: action={action}")
                    
            except Exception as e:
                logger.error(f"남은 에이전트 스트리밍 중 오류: {e}", exc_info=True)
                yield f"---FATAL_ERROR---\n{str(e)}\n"
        
        return StreamingResponse(generate_continuation_stream(), media_type="text/plain")
        
    except Exception as e:
        logger.error(f"남은 에이전트 실행 중 오류 발생: {e}", exc_info=True)
        return {
            "success": False,
            "content": f"처리 중 오류가 발생했습니다: {str(e)}",
            "agent_type": "error",
            "metadata": {}
        }


# =============================================================================
# 기존 엔드포인트 (deprecated - /message 사용 권장)
# =============================================================================

@router.post("/chat", deprecated=True)
async def chat_with_agent(chat_request: ChatRequest, request: Request):
    """[DEPRECATED] /message 엔드포인트 사용을 권장합니다."""
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
            supervisor_instance = get_supervisor()
            supervisor_response = await asyncio.wait_for(
                supervisor_instance.process_user_intent(user_intent),
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

async def _extract_and_store_keywords(user_id: int, message: str, message_id: int):
    """채팅 메시지에서 키워드를 추출하고 content_keywords에 저장 (백그라운드 태스크)"""
    if len(message.strip()) < 10:
        return  # 짧은 메시지는 스킵
    
    try:
        db = SQLite()
        keywords = []
        
        if _keyword_extractor and _keyword_extractor.is_available():
            # KeyBERT로 키워드 추출 (asyncio.to_thread로 블로킹 방지)
            extracted = await asyncio.to_thread(
                _keyword_extractor.extract, 
                message, 
                5,  # top_n
                (1, 2)  # keyphrase_ngram_range
            )
            keywords = list(set([kw for kw, _ in extracted]))
        else:
            # Fallback: 단순 휴리스틱 (2자 이상 토큰)
            tokens = message.split()
            keywords = list(set([t for t in tokens if len(t) > 2]))[:5]
        
        # content_keywords에 저장
        for kw in keywords:
            db.insert_content_keyword(
                user_id=user_id,
                source_type='chat',
                source_id=f"chat:{message_id}",
                keyword=kw,
                original_text=message[:200] if len(message) > 200 else message
            )
        
        if keywords:
            logger.debug(f"채팅 키워드 추출 완료: user_id={user_id}, keywords={keywords}")
    except Exception as e:
        logger.warning(f"채팅 키워드 추출 실패 (무시됨): {e}")

@router.post("/process", deprecated=True)
async def process_message(request_data: dict, request: Request):
    """[DEPRECATED] /message 엔드포인트 사용을 권장합니다."""
    try:
        message = request_data.get("message", "")
        user_id = request_data.get("user_id", 1)
        
        # 채팅 메시지 로깅 (사용자 메시지)
        db = SQLite()
        user_message_id = -1
        if message.strip():
            user_message_id = db.log_chat_message(
                user_id=user_id,
                role='user',
                content=message,
                metadata={"type": "user"}
            )
            # 백그라운드에서 키워드 추출 (10자 이상인 경우만)
            if user_message_id > 0 and len(message.strip()) >= 10:
                asyncio.create_task(_extract_and_store_keywords(user_id, message, user_message_id))
        
        # 스트리밍 요청 확인 (Accept 헤더)
        accept_header = request.headers.get("accept", "")
        stream_requested = "text/event-stream" in accept_header
        
        user_intent = UserIntent(message=message, user_id=user_id)
        
        if stream_requested:
            # 스트리밍 모드: supervisor를 통해 한 번만 처리
            logger.info("스트리밍 모드: supervisor를 통한 처리 시작")
            supervisor_instance = get_supervisor()
            supervisor_response = await asyncio.wait_for(
                supervisor_instance.process_user_intent(user_intent),
                timeout=settings.REQUEST_TIMEOUT
            )

            metadata = supervisor_response.metadata or {}
            agent_responses = metadata.get("agent_responses", [])

            if supervisor_response.success:
                content = supervisor_response.response.content or ""
                if not content.strip():
                    for agent_resp in agent_responses:
                        if agent_resp.get("success") and agent_resp.get("content"):
                            content = agent_resp["content"]
                            logger.warning("Supervisor 최종 응답이 비어 있어 첫 번째 에이전트 응답으로 대체했습니다.")
                            break
                if not content.strip():
                    logger.error("Supervisor 응답이 여전히 비어 있습니다. 기본 메시지를 반환합니다.")
                    content = "죄송합니다. 관련 응답을 생성하지 못했습니다."
                logger.info(f"Supervisor 응답 성공, 길이: {len(content)} 글자")
            else:
                error_msg = str(supervisor_response.response)
                logger.error(f"Supervisor 응답 실패: {error_msg}")
                content = f"처리 중 오류가 발생했습니다: {error_msg}"

            # Assistant 응답 로깅 (스트리밍 전에 로깅)
            agent_type = supervisor_response.response.agent_type if supervisor_response.success else "unknown"
            if len(content.strip()) >= 10:
                db.log_chat_message(
                    user_id=user_id,
                    role='assistant',
                    content=content,
                    metadata={"agent_type": agent_type, "success": supervisor_response.success}
                )

            # 에이전트 메타데이터 추출 (파일 열기 액션 등)
            # 부분 성공(일부 에이전트만 성공)인 경우에도 메타데이터는 추출해야 함
            response_metadata = {}
            if hasattr(supervisor_response.response, 'metadata'):
                response_metadata = supervisor_response.response.metadata or {}
            
            # 디버그: 메타데이터 로깅
            agent_type_debug = getattr(supervisor_response.response, 'agent_type', 'unknown')
            logger.info(f"[DEBUG] agent_type={agent_type_debug}")
            logger.info(f"[DEBUG] response_metadata={response_metadata}")
            
            async def generate_stream():
                try:
                    if not content:
                        yield "응답이 비어 있습니다."
                        return

                    chunk_size = 80
                    chunk_count = 0
                    total_length = len(content)
                    logger.info("스트리밍 응답 전송 시작 - 길이: %d", total_length)
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i:i + chunk_size]
                        chunk_count += 1
                        chunk_preview = chunk.replace("\n", "\\n")
                        if len(chunk_preview) > 80:
                            chunk_preview = chunk_preview[:80] + "..."
                        logger.debug(
                            "스트림 청크 #%d 전송 (len=%d, preview='%s')",
                            chunk_count,
                            len(chunk),
                            chunk_preview
                        )
                        yield chunk
                        await asyncio.sleep(0.01)
                    
                    # 메타데이터에 특정 action이 있으면 구분자와 함께 전송
                    action = response_metadata.get("action", "")
                    if action in ("open_file", "confirm_report", "request_topic", "confirm_analysis"):
                        import json as json_module
                        metadata_json = json_module.dumps(response_metadata, ensure_ascii=False)
                        yield f"\n\n---METADATA---\n{metadata_json}"
                        logger.info(f"스트리밍 메타데이터 전송: action={action}")
                    
                    logger.info(
                        "스트리밍 응답 전송 완료 - 총 %d개 청크",
                        chunk_count
                    )
                except Exception as e:
                    logger.error(f"스트리밍 응답 생성 중 오류: {e}", exc_info=True)
                    yield f"오류가 발생했습니다: {str(e)}"

            return StreamingResponse(generate_stream(), media_type="text/plain")
        else:
            # 비스트리밍 모드: 기존 로직 사용
            supervisor_instance = get_supervisor()
            supervisor_response = await asyncio.wait_for(
                supervisor_instance.process_user_intent(user_intent),
                timeout=settings.REQUEST_TIMEOUT
            )

            metadata = supervisor_response.metadata or {}
            agent_responses = metadata.get("agent_responses", [])

            if supervisor_response.success:
                content = supervisor_response.response.content or ""
                if not content.strip():
                    for agent_resp in agent_responses:
                        if agent_resp.get("success") and agent_resp.get("content"):
                            content = agent_resp["content"]
                            logger.warning("Supervisor 최종 응답이 비어 있어 첫 번째 에이전트 응답으로 대체했습니다.")
                            break
                if not content.strip():
                    logger.error("Supervisor 응답이 여전히 비어 있습니다. 기본 메시지를 반환합니다.")
                    content = "죄송합니다. 관련 응답을 생성하지 못했습니다."
            else:
                content = str(supervisor_response.response)
                if not content.strip():
                    content = "처리 중 오류가 발생했습니다."
            
            # Assistant 응답 로깅 (비스트리밍)
            agent_type = supervisor_response.response.agent_type if supervisor_response.success else "unknown"
            if len(content.strip()) >= 10:
                db.log_chat_message(
                    user_id=user_id,
                    role='assistant',
                    content=content,
                    metadata={"agent_type": agent_type, "success": supervisor_response.success}
                )
            
            # 프론트엔드가 기대하는 형식으로 반환
            return {
                "success": supervisor_response.success,
                "content": content,
                "agent_type": agent_type,
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
async def get_user_folders_endpoint(request: Request):
    """사용자의 기본 폴더(바탕화면, 문서, 다운로드) 목록을 조회합니다."""
    try:
        repository: Repository = getattr(request.app.state, "repository", None)
        embedder: BGEM3Embedder = getattr(request.app.state, "embedder", None)

        if repository is None or embedder is None:
            raise HTTPException(
                status_code=500,
                detail="Singleton resources are not initialised yet. Please retry shortly."
            )

        # TODO: 실제 인증 로직으로 사용자 ID를 추출하도록 교체
        user_id = getattr(request.state, "user_id", 1)

        manager = get_manager(user_id=user_id, repository=repository, embedder=embedder)
        folders = manager.file_collector.get_user_folders(calculate_size=True)
        return {
            "success": True,
            "folders": folders,
            "total_count": len(folders),
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("폴더 목록 조회 오류: %s", e, exc_info=True)
        return {
            "success": False,
            "message": f"폴더 목록 조회 오류: {str(e)}",
            "folders": [],
            "timestamp": datetime.utcnow().isoformat()
        }

@router.post("/data-collection/start/{user_id}")
async def start_data_collection(user_id: int, payload: Dict[str, List[str]], request: Request):
    """Starts data collection for a specific user."""
    try:
        selected_folders = payload.get("selected_folders")
        if not selected_folders:
            raise HTTPException(status_code=400, detail="You must select folders to scan.")

        # app.state에서 전역 인스턴스 가져오기
        repository = request.app.state.repository
        embedder = request.app.state.embedder
        
        manager = get_manager(user_id, repository=repository, embedder=embedder)

        # 초기 수집이 완료되지 않았다면 초기 수집만 실행 (중복 방지)
        if not manager.initial_collection_done:
            thread = threading.Thread(target=manager.perform_initial_collection, args=(selected_folders,), daemon=True)
            thread.start()
            # 초기 수집 중에는 백그라운드 수집을 시작하지 않음 (중복 파싱 방지)
            return {
                "success": True,
                "message": f"Initial data collection started for user {user_id}. The scan is running in the background.",
            }
        
        # 초기 수집이 이미 완료된 경우에만 백그라운드 수집 시작
        manager.start_collection(selected_folders)
        
        return {
            "success": True,
            "message": f"Background data collection started for user {user_id}.",
        }
    except Exception as e:
        logger.error(f"Error starting collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error starting collection: {str(e)}")

@router.post("/data-collection/stop/{user_id}")
async def stop_data_collection(user_id: int, request: Request):
    """Stops data collection for a specific user."""
    try:
        if user_id in data_collection_managers:
            # app.state에서 전역 인스턴스 가져오기
            repository = request.app.state.repository
            embedder = request.app.state.embedder
            
            manager = get_manager(user_id, repository=repository, embedder=embedder)
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
async def get_data_collection_status(user_id: int, request: Request):
    """Checks the data collection status (progress) for a user."""
    try:
        if user_id not in data_collection_managers:
            raise HTTPException(status_code=404, detail="No collection manager found for this user.")

        repository: Repository = getattr(request.app.state, "repository", None)
        embedder: BGEM3Embedder = getattr(request.app.state, "embedder", None)

        if repository is None or embedder is None:
            raise HTTPException(
                status_code=500,
                detail="Singleton resources are not initialised yet. Please retry shortly."
            )

        manager = get_manager(user_id=user_id, repository=repository, embedder=embedder)

        return {
            "progress": manager.progress,
            "progress_message": manager.progress_message,
            "is_done": manager.initial_collection_done,
            "running": manager.running
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("데이터 수집 상태 조회 오류: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"데이터 수집 상태 조회 오류: {str(e)}")

@router.get("/data-collection/stats")
async def get_data_collection_stats():
    """데이터 수집 통계를 확인합니다."""
    try:
        from database.sqlite import SQLite
        
        db = SQLite()
        
        # 각 테이블의 레코드 수 조회
        stats = db.get_collection_stats()
        file_count = stats.get('files', 0)
        browser_count = stats.get('browser_logs', 0)
        keyword_count = stats.get('content_keywords', 0)
        
        # 최근 24시간 내 데이터 수 (전체 데이터의 약 1/7을 최근 24시간으로 추정)
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        recent_files = file_count // 7
        recent_browser = browser_count // 7
        recent_keywords = keyword_count // 7
        
        return {
            "total_records": {
                "files": file_count,
                "browser_history": browser_count,
                "content_keywords": keyword_count
            },
            "last_24_hours": {
                "files": recent_files,
                "browser_history": recent_browser,
                "content_keywords": recent_keywords
            },
            "active_collectors": len(data_collection_managers),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"통계 조회 오류: {str(e)}")


# =========================================================================
# 파일 업로드 API (exe 클라이언트용)
# =========================================================================

@router.post("/files/upload/{user_id}")
async def upload_file_for_processing(
    user_id: int,
    request: Request,
    file: UploadFile = File(...),
    original_path: str = Form(...),
    file_category: str = Form("document")
):
    """
    클라이언트에서 업로드한 파일을 처리합니다. (exe 환경용)
    
    - 파일을 임시 저장
    - 파싱 및 청크 분할
    - Qdrant에 인덱싱
    - SQLite에 메타데이터 저장
    """
    try:
        repository: Repository = getattr(request.app.state, "repository", None)
        embedder: BGEM3Embedder = getattr(request.app.state, "embedder", None)
        
        if repository is None or embedder is None:
            raise HTTPException(
                status_code=500,
                detail="서버 리소스가 아직 초기화되지 않았습니다. 잠시 후 다시 시도해주세요."
            )
        
        # 파일 내용 읽기
        content = await file.read()
        file_size = len(content)
        
        # 파일 크기 제한 (50MB)
        if file_size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="파일 크기가 50MB를 초과합니다.")
        
        # 임시 파일로 저장 (파서가 파일 경로를 필요로 함)
        import os
        from pathlib import Path as PathLib
        
        file_ext = PathLib(file.filename).suffix.lower()
        temp_dir = tempfile.mkdtemp(prefix="jarvis_upload_")
        temp_path = os.path.join(temp_dir, file.filename)
        
        try:
            with open(temp_path, 'wb') as f:
                f.write(content)
            
            # 파일 해시 계산 (중복 체크용)
            file_hash = hashlib.md5(content).hexdigest()
            
            # SQLite에 파일 메타데이터 저장
            db = SQLite()
            file_info = {
                'user_id': user_id,
                'file_path': original_path,  # 원본 경로 저장
                'file_name': file.filename,
                'file_size': file_size,
                'file_category': file_category,
                'file_hash': file_hash,
                'modified_date': datetime.utcnow()
            }
            
            # 중복 체크
            if db.is_file_already_collected(user_id, original_path, file_hash):
                logger.info(f"파일 이미 수집됨, 스킵: {file.filename}")
                return {
                    "success": True,
                    "message": f"파일이 이미 수집되어 있습니다: {file.filename}",
                    "skipped": True
                }
            
            # 문서 파싱 및 청크 분할
            from database.document_parser import DocumentParser
            parser = DocumentParser()
            
            try:
                chunk_infos = parser.parse_and_chunk(temp_path)
            except Exception as e:
                logger.warning(f"파일 파싱 실패 ({file.filename}): {e}")
                return {
                    "success": False,
                    "message": f"파일 파싱 실패: {str(e)}",
                    "filename": file.filename
                }
            
            if not chunk_infos:
                logger.warning(f"청크 없음: {file.filename}")
                return {
                    "success": True,
                    "message": f"파일에서 텍스트를 추출할 수 없습니다: {file.filename}",
                    "chunks": 0
                }
            
            # 청크를 Qdrant에 인덱싱
            doc_id = f"file_{file_hash}"
            texts = []
            metas = []
            
            for chunk in chunk_infos:
                texts.append(chunk['text'])
                metas.append({
                    'user_id': user_id,
                    'source': 'file',
                    'path': original_path,
                    'doc_id': doc_id,
                    'chunk_id': chunk['chunk_id'],
                    'snippet': chunk['snippet'],
                    'content': chunk['text']
                })
            
            # 임베딩 및 Qdrant 업로드
            embeddings = embedder.encode_documents(texts)  # encode -> encode_documents
            dense_vectors = embeddings['dense_vecs'].tolist()
            sparse_vectors = [
                embedder.convert_sparse_to_qdrant_format(lw)
                for lw in embeddings['lexical_weights']
            ]
            repository.qdrant.upsert_vectors(metas, dense_vectors, sparse_vectors)
            
            # SQLite에 파일 메타데이터 저장
            db.insert_collected_file(file_info)
            
            # 키워드 추출 및 저장
            from database.data_collector import extract_keywords_from_text, create_snippet
            combined_text = '\n'.join(texts)
            
            if len(combined_text.strip()) >= 50:
                keywords = extract_keywords_from_text(combined_text, top_n=10)
                if keywords:
                    snippet = create_snippet(combined_text, max_length=200)
                    keyword_entries = []
                    for keyword, score in keywords:
                        keyword_entries.append({
                            'user_id': user_id,
                            'source_type': 'file',
                            'source_id': doc_id,
                            'keyword': keyword,
                            'original_text': snippet
                        })
                    db.insert_content_keywords_batch(user_id, keyword_entries)
            
            logger.info(f"✅ 파일 업로드 처리 완료: {file.filename} ({len(chunk_infos)}개 청크)")
            
            return {
                "success": True,
                "message": f"파일 처리 완료: {file.filename}",
                "filename": file.filename,
                "chunks": len(chunk_infos),
                "doc_id": doc_id
            }
            
        finally:
            # 임시 파일 정리
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"파일 업로드 처리 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"파일 처리 오류: {str(e)}")


@router.post("/files/upload-batch/{user_id}")
async def upload_files_batch(
    user_id: int,
    request: Request,
    files: List[UploadFile] = File(...),
    original_paths: str = Form(...)  # JSON 배열로 전달
):
    """
    여러 파일을 일괄 업로드합니다.
    original_paths는 JSON 배열 문자열로 전달됩니다.
    """
    import json
    
    try:
        paths = json.loads(original_paths)
    except:
        raise HTTPException(status_code=400, detail="original_paths는 JSON 배열이어야 합니다.")
    
    if len(files) != len(paths):
        raise HTTPException(
            status_code=400, 
            detail=f"파일 수({len(files)})와 경로 수({len(paths)})가 일치하지 않습니다."
        )
    
    results = []
    success_count = 0
    
    for file, path in zip(files, paths):
        try:
            # 개별 파일 처리 (단일 업로드 API 호출과 동일한 로직)
            result = await upload_file_for_processing(
                user_id=user_id,
                request=request,
                file=file,
                original_path=path,
                file_category="document"
            )
            results.append(result)
            if result.get("success"):
                success_count += 1
        except Exception as e:
            results.append({
                "success": False,
                "filename": file.filename,
                "error": str(e)
            })
    
    return {
        "success": True,
        "total": len(files),
        "success_count": success_count,
        "results": results
    }


@router.get("/user-survey/{user_id}")
async def get_user_survey(user_id: int):
    """사용자 설문지 응답을 조회합니다."""
    try:
        db = SQLite()
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
        db = SQLite()
        completed = db.has_user_completed_survey(user_id)
        
        return {
            "success": True,
            "completed": completed,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"설문지 완료 여부 확인 오류: {str(e)}")

@router.post("/user-profile/{user_id}/index")
async def index_user_profile(user_id: int, request: Request):
    """사용자 프로필을 Qdrant에 인덱싱합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        # 전역 싱글톤 인스턴스 사용
        indexer: UserProfileIndexer = request.app.state.profile_indexer
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
async def update_user_profile(user_id: int, survey_data: dict, request: Request):
    """사용자 설문조사를 업데이트하고 프로필을 재인덱싱합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        # SQLite에 설문조사 업데이트
        db = SQLite()
        success = db.insert_survey_response(user_id, survey_data)
        
        if not success:
            return {
                "success": False,
                "message": "설문조사 데이터 저장에 실패했습니다.",
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # 전역 싱글톤 인스턴스 사용
        indexer: UserProfileIndexer = request.app.state.profile_indexer
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
async def get_user_profile_context(user_id: int, request: Request):
    """사용자 프로필을 텍스트 형태로 반환합니다."""
    try:
        from database.user_profile_indexer import UserProfileIndexer
        
        # 전역 싱글톤 인스턴스 사용
        indexer: UserProfileIndexer = request.app.state.profile_indexer
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
        db = SQLite()
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
        db = SQLite()
        recommendations = db.get_all_recommendations(user_id)
        return {
            "success": True,
            "recommendations": recommendations
        }
    except Exception as e:
        logger.error(f"추천 내역 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendations/{recommendation_id}/read")
async def mark_recommendation_read(
    recommendation_id: int,
    user_id: int = Depends(get_current_user_id)
):
    """추천을 읽음으로 표시합니다."""
    try:
        db = SQLite()
        success = db.mark_recommendation_as_read(user_id, recommendation_id)
        if success:
            return {"success": True, "message": "Recommendation marked as read."}
        else:
            raise HTTPException(status_code=404, detail="Recommendation not found or failed to update.")
    except Exception as e:
        logger.error(f"추천 읽음 처리 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendations/{recommendation_id}/respond")
async def respond_to_recommendation(
    recommendation_id: int,
    request_data: dict,
    user_id: int = Depends(get_current_user_id)
):
    """
    추천에 대한 사용자 응답(수락/거절)을 처리합니다.
    
    - action='accept': 추천 수락 → 리포트 생성 후 반환
    - action='reject': 추천 거절 → 키워드 블랙리스트 추가
    
    응답 (accept 시):
        {
            "success": true,
            "action": "accept",
            "report_content": "...",
            "offer_deep_dive": true,  // 심층 보고서 제안 여부
            "keyword": "Python",
            "recommendation_id": 123
        }
    """
    try:
        action = request_data.get("action")
        if action not in ["accept", "reject"]:
            raise HTTPException(status_code=400, detail="action must be 'accept' or 'reject'")
        
        # 추천 에이전트 가져오기
        recommendation_agent = agent_registry.get_agent("recommendation")
        if not recommendation_agent:
            raise HTTPException(status_code=503, detail="추천 기능이 현재 사용 불가능합니다.")
        
        # 추천 소유권 확인
        db = SQLite()
        recommendation = db.get_recommendation(user_id, recommendation_id)
        if not recommendation:
            raise HTTPException(status_code=404, detail="추천을 찾을 수 없습니다.")
        
        keyword = recommendation.get("keyword", "")
        
        # handle_response 호출 (비동기)
        success, result_message = await recommendation_agent.handle_response(user_id, recommendation_id, action)
        
        if action == "accept" and success:
            # 수락 시: 심층 보고서 제안 포함
            return {
                "success": success,
                "action": action,
                "report_content": result_message,
                "offer_deep_dive": True,  # ReportAgent를 통한 심층 보고서 제안
                "keyword": keyword,
                "recommendation_id": recommendation_id
            }
        elif action == "reject":
            # 거절 시
            return {
                "success": success,
                "action": action,
                "message": result_message,
                "keyword": keyword,
                "recommendation_id": recommendation_id
            }
        else:
            # 실패 시
            return {
                "success": success,
                "action": action,
                "message": result_message if not success else None,
                "keyword": keyword,
                "recommendation_id": recommendation_id
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"추천 응답 처리 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"추천 응답 처리 중 오류가 발생했습니다: {str(e)}")

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
        db = SQLite()
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


# ============================================================================
# 보고서 생성 관련 엔드포인트
# ============================================================================

async def _create_report_background_task(
    user_id: int,
    keyword: str,
    recommendation_id: Optional[int] = None
):
    """
    백그라운드에서 보고서를 생성하고 완료 시 WebSocket으로 알림을 보냅니다.
    
    Args:
        user_id: 사용자 ID
        keyword: 보고서 주제 키워드
        recommendation_id: 연관된 추천 ID (선택)
    """
    from core.websocket_manager import get_websocket_manager
    from database.sqlite import SQLite
    
    logger.info(f"📄 보고서 생성 시작: user_id={user_id}, keyword='{keyword}'")
    
    try:
        # ReportAgent 가져오기
        report_agent = agent_registry.get_agent("report")
        if not report_agent:
            logger.error("ReportAgent를 찾을 수 없습니다.")
            ws_manager = get_websocket_manager()
            await ws_manager.broadcast_report_failed(
                user_id, keyword, "ReportAgent를 사용할 수 없습니다."
            )
            return
        
        # 보고서 생성 실행
        result = await report_agent.create_report(
            user_id=user_id,
            keyword=keyword,
            recommendation_id=recommendation_id
        )
        
        ws_manager = get_websocket_manager()
        
        if result.get("success"):
            # 성공: DB 업데이트 및 WebSocket 알림
            logger.info(f"📄 보고서 생성 완료: {result.get('pdf_filename')}")
            
            pdf_path = result.get("pdf_path", "")
            
            # recommendation_id가 있으면 report_file_path 업데이트
            if recommendation_id:
                db = SQLite()
                db.update_recommendation_report_path(
                    user_id,
                    recommendation_id, 
                    pdf_path
                )
            
            # 1. 먼저 WebSocket 알림 전송 (사용자에게 완료 알림)
            await ws_manager.broadcast_report_completed(
                user_id=user_id,
                keyword=keyword,
                file_path=pdf_path,
                file_name=result.get("pdf_filename", ""),
                sources=result.get("sources", [])
            )
            
            # 2. 그 후 보고서 파일을 SQLite와 Qdrant에 인덱싱 (채팅에서 검색 가능하도록)
            if pdf_path:
                try:
                    from utils.report_indexer import index_report_file_async
                    indexing_success = await index_report_file_async(
                        file_path=pdf_path,
                        user_id=user_id,
                        keyword=keyword
                    )
                    if indexing_success:
                        logger.info(f"📄 보고서 인덱싱 완료: {pdf_path}")
                    else:
                        logger.warning(f"📄 보고서 인덱싱 실패 (파일은 생성됨): {pdf_path}")
                except Exception as e:
                    logger.warning(f"📄 보고서 인덱싱 오류 (무시): {e}")
        else:
            # 실패: WebSocket 알림
            error_message = result.get("message", "알 수 없는 오류가 발생했습니다.")
            logger.error(f"📄 보고서 생성 실패: {error_message}")
            
            await ws_manager.broadcast_report_failed(
                user_id=user_id,
                keyword=keyword,
                reason=error_message
            )
            
    except Exception as e:
        logger.exception(f"📄 보고서 백그라운드 작업 오류: {e}")
        try:
            ws_manager = get_websocket_manager()
            await ws_manager.broadcast_report_failed(
                user_id=user_id,
                keyword=keyword,
                reason=str(e)
            )
        except Exception:
            pass


@router.post("/reports/create")
async def create_report(
    request_data: dict,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id)
):
    """
    보고서 생성 요청 (비동기 - 백그라운드 작업)
    
    요청 JSON:
        {
            "keyword": "보고서 주제",
            "recommendation_id": 123  // 선택적
        }
    
    응답:
        즉시 HTTP 202 반환, 완료 시 WebSocket으로 알림
    """
    keyword = request_data.get("keyword", "").strip()
    recommendation_id = request_data.get("recommendation_id")
    
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword는 필수입니다.")
    
    logger.info(f"📄 보고서 생성 요청 접수: user_id={user_id}, keyword='{keyword}'")
    
    # 백그라운드 작업으로 보고서 생성 시작
    background_tasks.add_task(
        _create_report_background_task,
        user_id=user_id,
        keyword=keyword,
        recommendation_id=recommendation_id
    )
    
    return {
        "success": True,
        "status": "queued",
        "message": f"'{keyword}' 보고서 생성이 시작되었습니다. 완료되면 알림을 보내드립니다.",
        "keyword": keyword,
        "recommendation_id": recommendation_id
    }


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
        
        db = SQLite()
        new_folder_path = request.get("new_folder_path", "")
        
        logger.info(f"사용자 {user_id}의 데이터 폴더 변경 시작...")
        logger.info(f"새 폴더 경로: {new_folder_path}")
        
        # 1. 데이터 수집 중지
        logger.info("데이터 수집 중지 중...")
        from database.data_collector import get_manager
        # app.state에서 전역 인스턴스 가져오기 (FastAPI Request 객체 필요)
        # 이 엔드포인트는 async 함수이므로 request_obj 파라미터 추가 필요
        # 임시로 기존 매니저가 있으면 중지만 수행
        from database.data_collector import data_collection_managers
        if user_id in data_collection_managers:
            manager = data_collection_managers[user_id]
            manager.stop_collection()
        
        # 2. SQLite에서 user_id 관련 데이터 삭제 (사용자 DB 파일)
        logger.info(f"SQLite에서 사용자 {user_id}의 데이터 삭제 중...")
        try:
            # 사용자별 DB 연결
            conn = db.get_user_connection(user_id)
            # files 테이블 삭제
            conn.execute("DELETE FROM files")
            # browser_logs 테이블 삭제
            conn.execute("DELETE FROM browser_logs")
            # content_keywords 테이블 삭제
            conn.execute("DELETE FROM content_keywords")
            # chat_messages 테이블 삭제
            conn.execute("DELETE FROM chat_messages")
            conn.commit()
            logger.info("SQLite 데이터 삭제 완료")
        except Exception as e:
            logger.error(f"SQLite 데이터 삭제 오류: {e}")
            conn = db.get_user_connection(user_id)
            if conn:
                conn.rollback()
        
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


# =============================================================================
# Dashboard API Endpoints
# =============================================================================

@router.get("/dashboard/summary")
async def get_dashboard_summary(user_id: int = Depends(get_current_user_id)):
    """대시보드 전체 요약 데이터 조회"""
    try:
        db = SQLite()
        
        # 사용자 정보
        user_info = db.get_user_by_id(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        
        # 관심사 요약
        interest_summary = db.get_interest_summary(user_id)
        
        # 활동 요약
        activity_summary = db.get_activity_summary(user_id)
        
        return {
            "success": True,
            "data": {
                "user": {
                    "user_id": user_info.get("user_id"),
                    "email": user_info.get("email"),
                    "selected_folder": user_info.get("selected_root_folder"),
                    "has_completed_setup": user_info.get("has_completed_setup", False),
                    "created_at": user_info.get("created_at")
                },
                "interests": interest_summary,
                "activity": activity_summary
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"대시보드 요약 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/interests")
async def get_dashboard_interests(
    user_id: int = Depends(get_current_user_id),
    days: int = 30,
    limit: int = 20
):
    """관심사 목록 및 트렌드 조회"""
    try:
        db = SQLite()
        
        # 현재 관심사 목록
        interests = db.get_user_interests(user_id, limit=limit)
        
        # 관심사 트렌드
        trend = db.get_interest_trend(user_id, days=days)
        
        return {
            "success": True,
            "data": {
                "interests": interests,
                "trend": trend
            }
        }
    except Exception as e:
        logger.error(f"관심사 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/notes")
async def get_dashboard_notes(
    user_id: int = Depends(get_current_user_id),
    limit: int = 50
):
    """노트 목록 조회"""
    try:
        db = SQLite()
        notes = db.get_notes(user_id, limit=limit)
        
        return {
            "success": True,
            "data": {"notes": notes}
        }
    except Exception as e:
        logger.error(f"노트 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dashboard/notes")
async def create_dashboard_note(
    request: Dict[str, Any],
    user_id: int = Depends(get_current_user_id)
):
    """새 노트 생성"""
    try:
        db = SQLite()
        
        content = request.get("content", "")
        title = request.get("title", "")
        pinned = request.get("pinned", False)
        tags = request.get("tags")
        
        if not content.strip():
            raise HTTPException(status_code=400, detail="노트 내용이 비어있습니다.")
        
        note_id = db.create_note(user_id, content, title, pinned, tags)
        
        if note_id:
            note = db.get_note_by_id(user_id, note_id)
            return {
                "success": True,
                "data": {"note": note}
            }
        else:
            raise HTTPException(status_code=500, detail="노트 생성에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노트 생성 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/dashboard/notes/{note_id}")
async def update_dashboard_note(
    note_id: int,
    request: Dict[str, Any],
    user_id: int = Depends(get_current_user_id)
):
    """노트 업데이트"""
    try:
        db = SQLite()
        
        content = request.get("content")
        title = request.get("title")
        pinned = request.get("pinned")
        tags = request.get("tags")
        
        success = db.update_note(user_id, note_id, content, title, pinned, tags)
        
        if success:
            note = db.get_note_by_id(user_id, note_id)
            return {
                "success": True,
                "data": {"note": note}
            }
        else:
            raise HTTPException(status_code=500, detail="노트 업데이트에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노트 업데이트 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/dashboard/notes/{note_id}")
async def delete_dashboard_note(
    note_id: int,
    user_id: int = Depends(get_current_user_id)
):
    """노트 삭제"""
    try:
        db = SQLite()
        success = db.delete_note(user_id, note_id)
        
        if success:
            return {"success": True, "message": "노트가 삭제되었습니다."}
        else:
            raise HTTPException(status_code=500, detail="노트 삭제에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"노트 삭제 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/activity")
async def get_dashboard_activity(
    user_id: int = Depends(get_current_user_id),
    days: int = 7
):
    """활동 요약 조회"""
    try:
        db = SQLite()
        activity = db.get_activity_summary(user_id, days=days)
        
        return {
            "success": True,
            "data": {"activity": activity}
        }
    except Exception as e:
        logger.error(f"활동 요약 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Dashboard AI Analysis Endpoints
# =============================================================================

@router.post("/dashboard/analyses/create")
async def create_dashboard_analysis(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id)
):
    """AI 분석 생성 요청"""
    try:
        from agents.dashboard_agent.dashboard_agent import DashboardAgent
        from core.websocket_manager import get_websocket_manager
        
        analysis_type = request.get("analysis_type", "custom")
        query = request.get("query", "")
        title = request.get("title", "데이터 분석")
        
        if not query:
            raise HTTPException(status_code=400, detail="분석 질문이 필요합니다.")
        
        # 백그라운드에서 분석 실행
        async def run_analysis():
            ws_manager = get_websocket_manager()
            try:
                agent = DashboardAgent()
                result = await agent.create_analysis(user_id, analysis_type, query)
                logger.info(f"분석 완료: user_id={user_id}, success={result.get('success')}")
                
                # WebSocket으로 알림 전송
                if result.get('success'):
                    await ws_manager.broadcast_analysis_completed(
                        user_id=user_id,
                        analysis_type=analysis_type,
                        title=title,
                        analysis_id=result.get('analysis_id')
                    )
                else:
                    await ws_manager.broadcast_analysis_failed(
                        user_id=user_id,
                        analysis_type=analysis_type,
                        title=title,
                        reason=result.get('message', '알 수 없는 오류')
                    )
            except Exception as e:
                logger.error(f"백그라운드 분석 오류: {e}", exc_info=True)
                await ws_manager.broadcast_analysis_failed(
                    user_id=user_id,
                    analysis_type=analysis_type,
                    title=title,
                    reason=str(e)
                )
        
        # 동기 래퍼로 백그라운드 태스크 추가
        def sync_run_analysis():
            import asyncio
            asyncio.run(run_analysis())
        
        background_tasks.add_task(sync_run_analysis)
        
        return {
            "success": True,
            "message": "분석을 시작했습니다. 완료되면 알림을 보내드립니다.",
            "data": {
                "analysis_type": analysis_type,
                "query": query,
                "title": title,
                "status": "processing"
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"분석 생성 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/analyses/latest")
async def get_latest_analysis(user_id: int = Depends(get_current_user_id)):
    """최신 분석 결과 조회 (대시보드 UI용)"""
    try:
        db = SQLite()
        analysis = db.get_latest_analysis(user_id)
        
        return {
            "success": True,
            "data": {"analysis": analysis}
        }
    except Exception as e:
        logger.error(f"최신 분석 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/analyses")
async def get_all_analyses(
    user_id: int = Depends(get_current_user_id),
    limit: int = 50
):
    """모든 분석 결과 목록 조회"""
    try:
        db = SQLite()
        analyses = db.get_all_analyses(user_id, limit=limit)
        
        return {
            "success": True,
            "data": {"analyses": analyses, "count": len(analyses)}
        }
    except Exception as e:
        logger.error(f"분석 목록 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/analyses/{analysis_id}")
async def get_analysis_by_id(
    analysis_id: int,
    user_id: int = Depends(get_current_user_id)
):
    """특정 분석 결과 조회"""
    try:
        db = SQLite()
        analysis = db.get_analysis_by_id(user_id, analysis_id)
        
        if not analysis:
            raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
        
        return {
            "success": True,
            "data": {"analysis": analysis}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"분석 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/dashboard/analyses/{analysis_id}")
async def delete_analysis(
    analysis_id: int,
    user_id: int = Depends(get_current_user_id)
):
    """분석 결과 삭제"""
    try:
        db = SQLite()
        success = db.delete_analysis(user_id, analysis_id)
        
        if success:
            return {"success": True, "message": "분석 결과가 삭제되었습니다."}
        else:
            raise HTTPException(status_code=500, detail="분석 결과 삭제에 실패했습니다.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"분석 삭제 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))