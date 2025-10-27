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
from .answerer import compose_answer
from database.repository import Repository
from utils.path_utils import get_config_path, get_base_path

logger = logging.getLogger(__name__)

# 프로젝트 루트 경로 (EXE 환경 호환)
PROJECT_ROOT = get_base_path()

# 싱글톤 인스턴스 (강화된 버전)
_repository: Optional[Repository] = None
_embedder: Optional[BGEM3Embedder] = None
_config: Optional[Dict[str, Any]] = None
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

def process(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    RAG 에이전트의 메인 처리 함수 (3-way 쿼리 라우터 포함)
    """
    try:
        question = state.get("question")
        if not question:
            return {**state, "answer": "질문이 제공되지 않았습니다.", "evidence": []}

        # 1. 쿼리 의도 분류 (3-way 라우터)
        path = _classify_query(question)

        # 2. 리소스 로드 (싱글톤)
        repo = _get_repository()
        embedder = _get_embedder()
        config = _get_config()
        
        k_final = config.get('retrieval', {}).get('k_final', 10)
        user_id = state.get("user_id")
        
        # user_id 필터 생성 (모든 검색에 필수)
        filters = state.get("filters") if state.get("filters") else {}
        if user_id:
            filters['user_id'] = user_id  # 필수: user_id로 데이터 격리

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
            
            answer = compose_answer(question, candidates, user_id)
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
                answer = compose_answer(question, None, user_id)
            
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
            
            answer = compose_answer(question, candidates, user_id)
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
            
            answer = compose_answer(question, candidates, user_id)
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