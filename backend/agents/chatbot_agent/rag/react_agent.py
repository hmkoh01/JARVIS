#!/usr/bin/env python3
import os
import yaml
import logging
from typing import Dict, Any, Optional
import threading
import time
from pathlib import Path

# BGE-M3와 관련된 핵심 모듈만 import
from .models.bge_m3_embedder import BGEM3Embedder
from .retrievers import retrieve_local
from .answerer import compose_answer, compose_answer_sync
from database.repository import Repository
from utils.path_utils import get_config_path, get_base_path

logger = logging.getLogger(__name__)

# 프로젝트 루트 경로 (EXE 환경 호환)
PROJECT_ROOT = get_base_path()

# 싱글톤 인스턴스 (강화된 버전) - 하위 호환성을 위해 유지
_repository: Optional[Repository] = None
_embedder: Optional[BGEM3Embedder] = None
_config: Optional[Dict[str, Any]] = None
_react_agent_instance: Optional['ReactAgent'] = None
_lock = threading.Lock()

# 모델 로딩 상태 추적
_model_loading = False
_model_loaded = False

def _load_config(config_path: str = "configs.yaml") -> Dict[str, Any]:
    """설정 로드 (EXE 환경 호환)"""
    try:
        config_file_path = get_config_path(config_path)
        
        if config_file_path.exists():
            with open(config_file_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                logger.info(f"설정 파일 로드 성공: {config_file_path}")
                return config
        else:
            logger.warning(f"설정 파일을 찾을 수 없습니다: {config_file_path}")
            return {}
            
    except Exception as e:
        logger.error(f"설정 파일 로드 실패: {e}")
        return {}

def _get_repository() -> Repository:
    """Repository 싱글톤 인스턴스 반환"""
    global _repository
    with _lock:
        if _repository is None:
            _repository = Repository()
    return _repository

def _get_embedder() -> BGEM3Embedder:
    """임베더 싱글톤 인스턴스 반환 (강화된 버전)"""
    global _embedder, _model_loading, _model_loaded
    
    with _lock:
        # 이미 로드된 경우 즉시 반환
        if _embedder is not None and _model_loaded:
            logger.debug("기존 BGE-M3 임베더 인스턴스 재사용")
            return _embedder
        
        # 다른 스레드에서 로딩 중인 경우 대기
        if _model_loading:
            logger.info("다른 스레드에서 BGE-M3 모델 로딩 중... 대기")
            while _model_loading:
                _lock.release()
                time.sleep(0.1)
                _lock.acquire()
            
            # 로딩 완료 후 다시 확인
            if _embedder is not None and _model_loaded:
                logger.debug("로딩 완료된 BGE-M3 임베더 인스턴스 사용")
                return _embedder
        
        # 새로 로딩 시작
        _model_loading = True
        logger.info("BGE-M3 임베더 초기화 시작...")
        
        try:
            _embedder = BGEM3Embedder()
            _model_loaded = True
            logger.info("BGE-M3 임베더 초기화 완료")
            return _embedder
        except Exception as e:
            logger.error(f"BGE-M3 임베더 초기화 실패: {e}")
            _embedder = None
            _model_loaded = False
            raise
        finally:
            _model_loading = False

def _get_config() -> Dict[str, Any]:
    """설정 싱글톤 인스턴스 반환"""
    global _config
    with _lock:
        if _config is None:
            _config = _load_config()
    return _config

class ReactAgent:
    """
    RAG 에이전트 클래스 (의존성 주입 지원)
    
    이 클래스는 Repository와 BGEM3Embedder를 생성자에서 주입받아 사용합니다.
    매 요청마다 모델을 로드하지 않고, 애플리케이션 시작 시 한 번만 초기화된 인스턴스를 재사용합니다.
    """
    
    def __init__(
        self,
        repository: Repository,
        embedder: BGEM3Embedder,
        config_path: str = "configs.yaml"
    ):
        """
        ReactAgent 초기화 (의존성 주입)
        
        Args:
            repository: Repository 인스턴스 (외부에서 생성되어 주입됨)
            embedder: BGEM3Embedder 인스턴스 (외부에서 생성되어 주입됨)
            config_path: 설정 파일 경로
        """
        logger.info("ReactAgent: Using pre-injected repository and embedder.")
        self.repository = repository
        self.embedder = embedder
        self.config_path = config_path
        self.config = _load_config(config_path)
        
        # 주입받은 객체를 사용하므로 여기서 새로 생성하지 않음
        # (X) self.embedder = BGEM3Embedder(...)  <-- 이런 코드 없음
        # (X) self.repository = Repository(...)   <-- 이런 코드 없음
    
    def _classify_query(self, user_query: str) -> str:
        """
        [개선됨] LLM 호출 없이 규칙 기반으로 쿼리 의도를 즉시 분류합니다.
        """
        query_lower = user_query.lower().strip()
        
        # 1. SUMMARY_QUERY 키워드 확인 (가장 구체적)
        summary_keywords = ["요약해줘", "요약", "핵심 내용", "개요", "전반적인"]
        if any(keyword in query_lower for keyword in summary_keywords):
            return "SUMMARY_QUERY"
            
        # 2. GENERAL_CHAT 키워드 확인 (간단한 인사, 질문)
        general_keywords = [
            "안녕", "안녕하세요", "안녕하셔요", "하이", "헬로", "헬로우",
            "누구야", "넌 누구니", "이름이 뭐야", "뭐하는", "뭐해",
            "날씨", "기분", "어때", "어떻게", "어떠해",
            "고마워", "감사", "반가워", "잘가", "수고했어", "농담", "재미",
            "좋은", "나쁜", "괜찮", "괜찮아", "괜찮나"
        ]
        # 예: "오늘 날씨 어때?" 또는 "안녕하세요"
        if len(query_lower.split()) < 7 and any(keyword in query_lower for keyword in general_keywords):
            return "GENERAL_CHAT"

        # 3. 그 외 모든 것은 STANDARD_RAG로 처리
        return "STANDARD_RAG"
    
    def process_stream(self, state: Dict[str, Any]):
        """
        RAG 에이전트의 스트리밍 처리 메서드 (3-way 쿼리 라우터 포함)
        RAG 검색을 먼저 수행한 후, 스트리밍 방식으로 답변을 생성합니다.
        """
        try:
            question = state.get("question")
            if not question:
                yield "질문이 제공되지 않았습니다."
                return

            # 1. 쿼리 의도 분류 (3-way 라우터) - 동기식으로 먼저 실행
            path = self._classify_query(question)

            # 2. 주입받은 리소스 사용 (이미 초기화됨)
            repo = self.repository
            embedder = self.embedder
            config = self.config
            
            k_final = config.get('retrieval', {}).get('k_final', 10)
            user_id = state.get("user_id")
            
            # user_id 필터 생성 (모든 검색에 필수)
            filters = state.get("filters") if state.get("filters") else {}
            if user_id:
                filters['user_id'] = user_id  # 필수: user_id로 데이터 격리

            # 사용자 프로필 가져오기 (dict 형태) - 동기식으로 먼저 실행
            user_profile = None
            if user_id:
                try:
                    from database.sqlite import SQLite
                    db = SQLite()
                    user_profile = db.get_user_survey_response(user_id)
                except Exception as e:
                    logger.warning(f"프로필 로드 실패: {e}")

            # 3. 분류에 따른 처리 분기 - RAG 검색 먼저 수행
            if path == "STANDARD_RAG":
                # 기존 RAG 검색 로직 - 동기식으로 먼저 실행
                logger.info("STANDARD_RAG 경로: 하이브리드 검색 수행 (스트리밍)")
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=filters
                )
                
                if not candidates:
                    yield "관련 정보를 찾을 수 없습니다."
                    return
                
                # 검색된 evidence로 스트리밍 답변 생성
                logger.info("검색된 evidence로 스트리밍 답변 생성 시작")
                yield from compose_answer(question, candidates, user_id, user_profile)
                
            elif path == "GENERAL_CHAT":
                # 일반 대화 로직 (빠른 응답을 위한 최적화)
                logger.info("GENERAL_CHAT 경로: 일반 대화 처리 (스트리밍)")
                
                # 간단한 인사말의 경우 사전 정의된 답변 사용
                question_lower = question.lower().strip()
                if any(greeting in question_lower for greeting in ["안녕", "안녕하세요", "하이", "헬로"]):
                    yield "안녕하세요! 저는 JARVIS AI 어시스턴트입니다. 무엇을 도와드릴까요?"
                    return
                elif any(identity in question_lower for identity in ["누구야", "넌 누구니", "이름이 뭐야", "뭐하는", "뭐해"]):
                    yield "저는 JARVIS AI 어시스턴트입니다. 사용자의 파일과 웹 활동을 분석하여 맞춤형 도움을 제공합니다."
                    return
                elif any(thanks in question_lower for thanks in ["고마워", "감사", "수고했어"]):
                    yield "천만에요! 언제든 도움이 필요하시면 말씀해주세요."
                    return
                else:
                    # 복잡한 일반 대화는 Gemini API 사용 (스트리밍)
                    yield from compose_answer(question, None, user_id, user_profile)
                
            elif path == "SUMMARY_QUERY":
                # 요약 쿼리 로직 (메타데이터 필터 적용) - 동기식으로 먼저 실행
                logger.info("SUMMARY_QUERY 경로: 요약 검색 수행 (스트리밍)")
                summary_filters = filters.copy()  # user_id 필터 포함하여 복사
                summary_filters['type'] = 'summary'  # 요약 관련 문서만 검색
                
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=summary_filters
                )
                
                if not candidates:
                    yield "요약할 정보를 찾을 수 없습니다."
                    return
                
                # 검색된 evidence로 스트리밍 답변 생성
                yield from compose_answer(question, candidates, user_id, user_profile)
            
            else:
                # 예상하지 못한 분류 결과 - 폴백: STANDARD_RAG로 처리
                logger.warning(f"예상하지 못한 분류 결과: {path}, STANDARD_RAG로 폴백 (스트리밍)")
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=filters  # user_id 필터 포함
                )
                
                if not candidates:
                    yield "관련 정보를 찾을 수 없습니다."
                    return
                
                # 검색된 evidence로 스트리밍 답변 생성
                yield from compose_answer(question, candidates, user_id, user_profile)
            
        except Exception as e:
            logger.error(f"ReAct 에이전트 스트리밍 처리 중 심각한 오류 발생: {e}", exc_info=True)
            yield f"답변 생성 중 오류가 발생했습니다: {str(e)}"
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        RAG 에이전트의 메인 처리 메서드 (3-way 쿼리 라우터 포함) - 동기식 버전
        """
        try:
            question = state.get("question")
            if not question:
                return {**state, "answer": "질문이 제공되지 않았습니다.", "evidence": []}

            # 1. 쿼리 의도 분류 (3-way 라우터)
            path = self._classify_query(question)

            # 2. 주입받은 리소스 사용 (이미 초기화됨)
            repo = self.repository
            embedder = self.embedder
            config = self.config
            
            k_final = config.get('retrieval', {}).get('k_final', 10)
            user_id = state.get("user_id")
            
            # user_id 필터 생성 (모든 검색에 필수)
            filters = state.get("filters") if state.get("filters") else {}
            if user_id:
                filters['user_id'] = user_id  # 필수: user_id로 데이터 격리

            # 사용자 프로필 가져오기 (dict 형태)
            user_profile = None
            if user_id:
                try:
                    from database.sqlite import SQLite
                    db = SQLite()
                    user_profile = db.get_user_survey_response(user_id)
                except Exception as e:
                    logger.warning(f"프로필 로드 실패: {e}")

            # 3. 분류에 따른 처리 분기
            if path == "STANDARD_RAG":
                # 기존 RAG 검색 로직
                logger.info("STANDARD_RAG 경로: 하이브리드 검색 수행")
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=filters
                )
                
                if not candidates:
                    return {**state, "answer": "관련 정보를 찾을 수 없습니다.", "evidence": []}
                
                answer = compose_answer_sync(question, candidates, user_id, user_profile)
                return {
                    **state,
                    "answer": answer,
                    "evidence": candidates,
                    "routing_path": path
                }
                
            elif path == "GENERAL_CHAT":
                # 일반 대화 로직 (빠른 응답을 위한 최적화)
                logger.info("GENERAL_CHAT 경로: 일반 대화 처리")
                
                # 간단한 인사말의 경우 사전 정의된 답변 사용
                question_lower = question.lower().strip()
                if any(greeting in question_lower for greeting in ["안녕", "안녕하세요", "하이", "헬로"]):
                    answer = "안녕하세요! 저는 JARVIS AI 어시스턴트입니다. 무엇을 도와드릴까요?"
                elif any(identity in question_lower for identity in ["누구야", "넌 누구니", "이름이 뭐야", "뭐하는", "뭐해"]):
                    answer = "저는 JARVIS AI 어시스턴트입니다. 사용자의 파일과 웹 활동을 분석하여 맞춤형 도움을 제공합니다."
                elif any(thanks in question_lower for thanks in ["고마워", "감사", "수고했어"]):
                    answer = "천만에요! 언제든 도움이 필요하시면 말씀해주세요."
                else:
                    # 복잡한 일반 대화는 Gemini API 사용
                    answer = compose_answer_sync(question, None, user_id, user_profile)
                
                return {
                    **state,
                    "answer": answer,
                    "evidence": [],
                    "routing_path": path
                }
                
            elif path == "SUMMARY_QUERY":
                # 요약 쿼리 로직 (메타데이터 필터 적용)
                logger.info("SUMMARY_QUERY 경로: 요약 검색 수행")
                summary_filters = filters.copy()  # user_id 필터 포함하여 복사
                summary_filters['type'] = 'summary'  # 요약 관련 문서만 검색
                
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=summary_filters
                )
                
                if not candidates:
                    return {**state, "answer": "요약할 정보를 찾을 수 없습니다.", "evidence": []}
                
                answer = compose_answer_sync(question, candidates, user_id, user_profile)
                return {
                    **state,
                    "answer": answer,
                    "evidence": candidates,
                    "routing_path": path
                }
            
            else:
                # 예상하지 못한 분류 결과
                logger.warning(f"예상하지 못한 분류 결과: {path}, STANDARD_RAG로 폴백")
                candidates = retrieve_local(
                    question=question,
                    repo=repo,
                    embedder=embedder,
                    k_final=k_final,
                    filters=filters  # user_id 필터 포함
                )
                
                if not candidates:
                    return {**state, "answer": "관련 정보를 찾을 수 없습니다.", "evidence": []}
                
                answer = compose_answer_sync(question, candidates, user_id, user_profile)
                return {
                    **state,
                    "answer": answer,
                    "evidence": candidates,
                    "routing_path": "STANDARD_RAG_FALLBACK"
                }
            
        except Exception as e:
            logger.error(f"ReAct 에이전트 처리 중 심각한 오류 발생: {e}", exc_info=True)
            return {
                **state,
                "answer": f"답변 생성 중 오류가 발생했습니다.",
                "evidence": [],
                "routing_path": "ERROR"
            }

# 하위 호환성을 위한 함수 래퍼 (기존 코드와의 호환성 유지)
def _classify_query(user_query: str) -> str:
    """
    [개선됨] LLM 호출 없이 규칙 기반으로 쿼리 의도를 즉시 분류합니다.
    """
    query_lower = user_query.lower().strip()
    
    # 1. SUMMARY_QUERY 키워드 확인 (가장 구체적)
    summary_keywords = ["요약해줘", "요약", "핵심 내용", "개요", "전반적인"]
    if any(keyword in query_lower for keyword in summary_keywords):
        return "SUMMARY_QUERY"
        
    # 2. GENERAL_CHAT 키워드 확인 (간단한 인사, 질문)
    general_keywords = [
        "안녕", "안녕하세요", "안녕하셔요", "하이", "헬로", "헬로우",
        "누구야", "넌 누구니", "이름이 뭐야", "뭐하는", "뭐해",
        "날씨", "기분", "어때", "어떻게", "어떠해",
        "고마워", "감사", "반가워", "잘가", "수고했어", "농담", "재미",
        "좋은", "나쁜", "괜찮", "괜찮아", "괜찮나"
    ]
    # 예: "오늘 날씨 어때?" 또는 "안녕하세요"
    if len(query_lower.split()) < 7 and any(keyword in query_lower for keyword in general_keywords):
        return "GENERAL_CHAT"

    # 3. 그 외 모든 것은 STANDARD_RAG로 처리
    return "STANDARD_RAG"

def set_global_react_agent(agent: ReactAgent):
    """
    전역 ReactAgent 인스턴스를 설정합니다.
    main.py에서 앱 시작 시 초기화된 인스턴스를 설정하기 위해 사용됩니다.
    """
    global _react_agent_instance
    with _lock:
        _react_agent_instance = agent
        logger.info("전역 ReactAgent 인스턴스가 설정되었습니다.")

def _get_react_agent() -> ReactAgent:
    """ReactAgent 싱글톤 인스턴스 반환 (하위 호환성)"""
    global _react_agent_instance
    with _lock:
        if _react_agent_instance is None:
            # 싱글톤이 없으면 기존 방식으로 생성 (하위 호환성)
            logger.warning("전역 ReactAgent 인스턴스가 설정되지 않았습니다. lazy loading으로 생성합니다.")
            repo = _get_repository()
            embedder = _get_embedder()
            _react_agent_instance = ReactAgent(
                repository=repo,
                embedder=embedder,
                config_path="configs.yaml"
            )
    return _react_agent_instance

def process_stream(state: Dict[str, Any]):
    """
    RAG 에이전트의 스트리밍 처리 함수 (3-way 쿼리 라우터 포함)
    하위 호환성을 위한 래퍼 함수 - 내부에서 싱글톤 ReactAgent 인스턴스를 사용합니다.
    """
    agent = _get_react_agent()
    yield from agent.process_stream(state)

def process(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    RAG 에이전트의 메인 처리 함수 (3-way 쿼리 라우터 포함) - 동기식 버전
    하위 호환성을 위한 래퍼 함수 - 내부에서 싱글톤 ReactAgent 인스턴스를 사용합니다.
    """
    agent = _get_react_agent()
    return agent.process(state)