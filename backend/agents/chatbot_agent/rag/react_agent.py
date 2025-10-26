#!/usr/bin/env python3
import os
import yaml
import logging
from typing import Dict, Any, Optional
import threading
from pathlib import Path

# BGE-M3와 관련된 핵심 모듈만 import
from .models.bge_m3_embedder import BGEM3Embedder
from .retrievers import retrieve_local
from .answerer import compose_answer
from database.repository import Repository

logger = logging.getLogger(__name__)

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent

# 싱글톤 인스턴스 (기존과 동일)
_repository: Optional[Repository] = None
_embedder: Optional[BGEM3Embedder] = None
_config: Optional[Dict[str, Any]] = None
_lock = threading.Lock()

def _load_config(config_path: str = "configs.yaml") -> Dict[str, Any]:
    """설정 로드"""
    absolute_config_path = PROJECT_ROOT / config_path
    if os.path.exists(absolute_config_path):
        with open(absolute_config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    logger.warning(f"configs.yaml을 찾을 수 없어 기본 설정을 사용합니다. (경로: {absolute_config_path})")
    return {} # 기본값은 각 함수에서 처리

def _get_repository() -> Repository:
    """Repository 싱글톤 인스턴스 반환"""
    global _repository
    with _lock:
        if _repository is None:
            _repository = Repository()
    return _repository

def _get_embedder() -> BGEM3Embedder:
    """임베더 싱글톤 인스턴스 반환"""
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = BGEM3Embedder()
    return _embedder

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
    logger.info("규칙 기반 쿼리 분류 시작...")
    
    query_lower = user_query.lower().strip()
    
    # 1. SUMMARY_QUERY 키워드 확인 (가장 구체적)
    summary_keywords = ["요약해줘", "요약", "핵심 내용", "개요", "전반적인"]
    if any(keyword in query_lower for keyword in summary_keywords):
        logger.info("분류 결과: SUMMARY_QUERY (키워드 매칭)")
        return "SUMMARY_QUERY"
        
    # 2. GENERAL_CHAT 키워드 확인 (간단한 인사, 질문)
    # (질문의 길이가 짧고, 특정 키워드를 포함하는 경우)
    general_keywords = [
        "안녕", "누구야", "넌 누구니", "이름이 뭐야", "날씨", "기분",
        "고마워", "반가워", "잘가", "수고했어", "농담"
    ]
    # 예: "오늘 날씨 어때?"
    if len(query_lower.split()) < 7 and any(keyword in query_lower for keyword in general_keywords):
        logger.info("분류 결과: GENERAL_CHAT (키워드 매칭)")
        return "GENERAL_CHAT"

    # 3. 그 외 모든 것은 STANDARD_RAG로 처리
    logger.info("분류 결과: STANDARD_RAG (기본값)")
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
        logger.info(f"쿼리 의도 분류 시작: \"{question}\"")
        path = _classify_query(question)
        logger.info(f"분류 결과: {path}")

        # 2. 리소스 로드 (싱글톤)
        repo = _get_repository()
        embedder = _get_embedder()
        config = _get_config()
        
        k_final = config.get('retrieval', {}).get('k_final', 10)
        filters = state.get("filters") # user_id 등 필요한 필터
        user_id = state.get("user_id")

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
            # 일반 대화 로직 (검색 없음)
            logger.info("GENERAL_CHAT 경로: 일반 대화 처리")
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
            summary_filters = filters.copy() if filters else {}
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
                filters=filters
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