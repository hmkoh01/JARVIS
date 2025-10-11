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

def process(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    RAG 에이전트의 메인 처리 함수 (BGE-M3 하이브리드 검색에 최적화됨)
    """
    try:
        question = state.get("question")
        if not question:
            return {**state, "answer": "질문이 제공되지 않았습니다.", "evidence": []}

        # 1. 리소스 로드 (싱글톤)
        repo = _get_repository()
        embedder = _get_embedder()
        config = _get_config()
        
        k_final = config.get('retrieval', {}).get('k_final', 10)
        filters = state.get("filters") # user_id 등 필요한 필터

        # 2. 검색 및 순위 결정 (Retrieve & Rank)
        # retrieve_local 함수가 내부적으로 BGE-M3의 하이브리드 검색과 RRF 랭킹을 모두 수행합니다.
        # 이 단계에서 이미 최적의 순위가 결정됩니다.
        logger.info(f"질문으로 하이브리드 검색 시작: \"{question}\"")
        candidates = retrieve_local(
            question=question,
            repo=repo,
            embedder=embedder,
            k_final=k_final,
            filters=filters
        )
        
        if not candidates:
            return {**state, "answer": "관련 정보를 찾을 수 없습니다.", "evidence": []}
        
        # (제거됨) 2차 리랭킹(reranking) 과정이 더 이상 필요하지 않습니다.

        # 3. 답변 생성 (Synthesize)
        # 검색된 최상위 문서를 바탕으로 최종 답변을 생성합니다.
        user_id = state.get("user_id")
        answer = compose_answer(question, candidates, user_id)
        
        # 4. 결과 반환
        return {
            **state,
            "answer": answer,
            "evidence": candidates
        }
        
    except Exception as e:
        logger.error(f"ReAct 에이전트 처리 중 심각한 오류 발생: {e}", exc_info=True)
        return {
            **state,
            "answer": f"답변 생성 중 오류가 발생했습니다.",
            "evidence": []
        }