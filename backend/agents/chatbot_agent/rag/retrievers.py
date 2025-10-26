import os
import yaml
from typing import List, Dict, Any, Optional
import numpy as np
import logging
from .models.bge_m3_embedder import BGEM3Embedder
from database.repository import Repository

logger = logging.getLogger(__name__)

def retrieve_local(question: str, repo: Repository, embedder: BGEM3Embedder, 
                  k_candidates: int = 40, k_final: int = 10, 
                  filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """로컬 하이브리드 RAG 검색 (Dense + Sparse)"""
    logger.info(f"하이브리드 RAG 검색 시작 - 질문: {question[:100]}...")
    logger.debug(f"검색 파라미터 - k_candidates: {k_candidates}, k_final: {k_final}")
    
    try:
        # 1. 질의 벡터 생성 (Dense + Sparse)
        logger.debug("질의 임베딩 생성 시작 (BGE-M3)")
        query_embeddings = embedder.encode_single_query(question)
        
        if not query_embeddings or 'dense_vec' not in query_embeddings:
            logger.error("질의 벡터 생성 실패")
            return []
        
        query_dense = query_embeddings['dense_vec'].tolist()
        query_sparse = embedder.convert_sparse_to_qdrant_format(query_embeddings['sparse_vec'])
        
        logger.debug(f"Dense 벡터 생성 완료: {len(query_dense)} 차원")
        logger.debug(f"Sparse 벡터 생성 완료: {len(query_sparse.get('indices', []))} 토큰")
        
        # 2. 하이브리드 검색 (Qdrant에서 Dense + Sparse 통합 검색)
        logger.debug("하이브리드 검색 시작")
        candidates = repo.search_hybrid(
            query_dense=query_dense,
            query_sparse=query_sparse,
            limit=k_candidates,
            source_filter=None,  # 모든 소스 검색
            query_filter=filters  # 필터 전달
        )
        
        if not candidates:
            logger.warning("검색 결과가 없습니다")
            return []
        
        logger.info(f"하이브리드 검색 결과: {len(candidates)}개")
        
        # 3. Hit 객체를 딕셔너리로 변환
        result_dicts = []
        for hit in candidates:
            result_dicts.append({
                'doc_id': hit.doc_id,
                'score': hit.score,
                'source': hit.source,
                'page': hit.page,
                'bbox': hit.bbox,
                'path': hit.path,
                'url': hit.url,
                'timestamp': hit.timestamp,
                'snippet': hit.snippet
            })
        
        # 4. 상위 k_final 반환
        top_candidates = result_dicts[:k_final]
        
        logger.info(f"검색 완료: {len(top_candidates)}개 결과 반환")
        if top_candidates:
            logger.debug(f"상위 결과 점수: {[c['score'] for c in top_candidates[:3]]}")
        
        return top_candidates
        
    except Exception as e:
        logger.error(f"하이브리드 검색 오류: {e}")
        import traceback
        traceback.print_exc()
        return []

def maxsim_score(query_vecs: np.ndarray, doc_vecs: np.ndarray) -> float:
    """ColBERT 스타일 MaxSim 점수 계산"""
    try:
        if query_vecs.size == 0 or doc_vecs.size == 0:
            logger.warning("빈 벡터로 인한 MaxSim 점수 계산 실패")
            return 0.0
        
        # 코사인 유사도 계산
        query_norm = np.linalg.norm(query_vecs, axis=1, keepdims=True)
        doc_norm = np.linalg.norm(doc_vecs, axis=1, keepdims=True)
        
        # 정규화
        query_vecs_norm = query_vecs / (query_norm + 1e-8)
        doc_vecs_norm = doc_vecs / (doc_norm + 1e-8)
        
        # 유사도 행렬 계산
        similarity_matrix = np.dot(query_vecs_norm, doc_vecs_norm.T)
        
        # MaxSim: 각 쿼리 벡터에 대해 문서 벡터들과의 최대 유사도 구하기
        max_similarities = np.max(similarity_matrix, axis=1)
        
        # 모든 쿼리 벡터의 최대 유사도 합
        total_score = np.sum(max_similarities)
        
        logger.debug(f"MaxSim 점수 계산 완료: {total_score}")
        return float(total_score)
        
    except Exception as e:
        logger.error(f"MaxSim 점수 계산 오류: {e}")
        return 0.0
