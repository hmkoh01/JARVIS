import os
import yaml
import uuid
from typing import List, Dict, Any, Union, Optional
import numpy as np
from qdrant_client import QdrantClient, models
import logging
from pathlib import Path
from utils.path_utils import get_config_path

PROJECT_ROOT = Path(__file__).parent.parent.parent
logger = logging.getLogger(__name__)

class QdrantManager:
    """Qdrant 벡터 DB 하이브리드 검색 관리 클래스"""
    
    def __init__(self, config_path: str = "configs.yaml"):
        """초기화: 설정 로드, 클라이언트 연결, 하이브리드 컬렉션 확인/생성"""
        config_file_path = get_config_path(config_path)
        self.config = self._load_config(config_file_path)
        qdrant_config = self.config['qdrant']
        self.client = QdrantClient(url=qdrant_config['url'])
        self.collection_name = qdrant_config['collection_name']
        self.embedding_dim = self.config['embedding'].get('dense_dim', 1024)
        self.retrieval_weights = self.config['retrieval']['weights']
        
        self._ensure_hybrid_collection()
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """설정 파일 로드"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            else:
                # 기본 설정
                logger.warning(f"설정 파일 '{config_path}'를 찾을 수 없어 기본 설정을 사용합니다.")
                return {
                    'qdrant': {
                        'url': 'http://localhost:6333',
                        'collection_name': 'user_context'
                    },
                    'embedding': {
                        'model_name': 'BAAI/bge-m3',
                        'dense_dim': 1024,
                        'batch_size': 12
                    },
                    'sqlite': {
                        'path': 'sqlite.db'
                    },
                    'retrieval': {
                        'k_candidates': 40,
                        'k_final': 10,
                        'weights': {
                            'dense': 0.6,
                            'sparse': 0.4
                        }
                    }
                }
        except Exception as e:
            logger.error(f"설정 로드 오류: {e}")
            return {
                'qdrant': {
                    'url': 'http://localhost:6333',
                    'collection_name': 'user_context'
                },
                'embedding': {
                    'model_name': 'BAAI/bge-m3',
                    'dense_dim': 1024,
                    'batch_size': 12
                },
                'sqlite': {
                    'path': 'sqlite.db'
                },
                'retrieval': {
                    'k_candidates': 40,
                    'k_final': 10,
                    'weights': {
                        'dense': 0.6,
                        'sparse': 0.4
                    }
                }
            }

    def _ensure_hybrid_collection(self):
        """Dense와 Sparse 벡터를 모두 지원하는 단일 컬렉션 확인/생성"""
        try:
            self.client.get_collection(collection_name=self.collection_name)
            logger.info(f"하이브리드 컬렉션 '{self.collection_name}'이 이미 존재합니다.")
        except Exception:
            logger.warning(f"컬렉션 '{self.collection_name}'을 찾을 수 없어 새로 생성합니다.")
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": models.VectorParams(size=self.embedding_dim, distance=models.Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(index=models.SparseIndexParams(on_disk=False))
                }
            )
            logger.info(f"하이브리드 컬렉션 '{self.collection_name}' 생성 완료.")
    
    def upsert_vectors(self, 
                     payloads: List[Dict[str, Any]],
                     dense_vectors: List[List[float]],
                     sparse_vectors: List[Dict[str, List]]) -> bool:
        """Dense, Sparse 벡터와 페이로드를 함께 업서트"""
        try:
            points = []
            for payload, dense_vec, sparse_vec_data in zip(payloads, dense_vectors, sparse_vectors):
                # Qdrant의 SparseVector 형식으로 변환
                sparse_vec = models.SparseVector(
                    indices=sparse_vec_data['indices'],
                    values=sparse_vec_data['values']
                )
                
                point = models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"dense": dense_vec, "sparse": sparse_vec},
                    payload=payload
                )
                points.append(point)

            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True
            )
            logger.info(f"컬렉션 '{self.collection_name}'에 {len(points)}개 하이브리드 포인트 업서트 완료")
            return True
        except Exception as e:
            logger.error(f"하이브리드 벡터 업서트 오류: {e}")
            return False

    def hybrid_search(self, 
                      query_dense: List[float], 
                      query_sparse: Dict[str, List],
                      limit: int,
                      query_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """하이브리드 검색 (Dense + Sparse) 후 RRF로 결과 융합 (개선된 버전)"""
        
        try:
            # 1. 입력 검증
            if not query_dense or len(query_dense) == 0:
                logger.error("빈 dense 벡터로 검색할 수 없습니다.")
                return []
            
            if not query_sparse or not query_sparse.get('indices') or not query_sparse.get('values'):
                logger.warning("Sparse 벡터가 비어있어 dense 검색만 수행합니다.")
                return self._dense_only_search(query_dense, limit, query_filter)
            
            # 2. SparseVector 생성 (안전하게)
            try:
                sparse_vector = models.SparseVector(
                    indices=query_sparse['indices'],
                    values=query_sparse['values']
                )
            except Exception as e:
                logger.error(f"SparseVector 생성 실패: {e}")
                return self._dense_only_search(query_dense, limit, query_filter)
            
            # 3. Qdrant 필터 생성 (안전하게)
            qdrant_filter = None
            if query_filter:
                try:
                    conditions = []
                    for key, value in query_filter.items():
                        if isinstance(value, str):
                            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
                        elif isinstance(value, list):
                            conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
                        else:
                            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
                    
                    if conditions:
                        qdrant_filter = models.Filter(must=conditions)
                except Exception as e:
                    logger.warning(f"필터 생성 실패, 필터 없이 검색: {e}")
                    qdrant_filter = None
            
            # 4. 두 검색을 한 번에 실행 (네트워크 효율성)
            try:
                # SearchRequest 생성 시 올바른 파라미터 사용
                request_kwargs = {}
                if qdrant_filter:
                    request_kwargs['filter'] = qdrant_filter

                dense_request = models.SearchRequest(
                    vector=models.NamedVector(
                        name="dense",
                        vector=query_dense
                    ),
                    limit=limit,
                    with_payload=True,
                    params=models.SearchParams(hnsw_ef=128),
                    **request_kwargs
                )
                
                sparse_request = models.SearchRequest(
                    vector=models.NamedSparseVector(
                        name="sparse",
                        vector=sparse_vector
                    ),
                    limit=limit,
                    with_payload=True,
                    **request_kwargs
                )
                
                dense_results, sparse_results = self.client.search_batch(
                    collection_name=self.collection_name,
                    requests=[dense_request, sparse_request]
                )
            except Exception as e:
                logger.error(f"하이브리드 검색 오류: {e}")
                # 하이브리드 검색 실패 시 dense 검색만 시도
                return self._dense_only_search(query_dense, limit, query_filter)

            # 5. 결과 융합 (Reciprocal Rank Fusion)
            fused_results = self._reciprocal_rank_fusion(
                [dense_results, sparse_results],
                [self.retrieval_weights['dense'], self.retrieval_weights['sparse']]
            )
            
            # 6. 최종 결과 반환 (상위 limit 개)
            return fused_results[:limit]
            
        except Exception as e:
            logger.error(f"하이브리드 검색 전체 오류: {e}")
            return []

    def _dense_only_search(self, query_dense: List[float], limit: int, query_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Dense 벡터만 사용한 검색 (fallback)"""
        try:
            # Qdrant 필터 생성
            qdrant_filter = None
            if query_filter:
                try:
                    conditions = []
                    for key, value in query_filter.items():
                        if isinstance(value, str):
                            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
                        elif isinstance(value, list):
                            conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
                        else:
                            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
                    
                    if conditions:
                        qdrant_filter = models.Filter(must=conditions)
                except Exception as e:
                    logger.warning(f"Dense 검색 필터 생성 실패: {e}")
                    qdrant_filter = None
            
            # Dense 검색만 수행
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=models.NamedVector(name="dense", vector=query_dense),
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
                search_params=models.SearchParams(hnsw_ef=128)
            )
            
            # 결과를 표준 형식으로 변환
            return [
                {'id': result.id, 'score': result.score, 'payload': result.payload}
                for result in results
            ]
            
        except Exception as e:
            logger.error(f"Dense 검색 오류: {e}")
            return []

    def _reciprocal_rank_fusion(self, results_lists: List[List[models.ScoredPoint]], weights: List[float], k: int = 60) -> List[Dict[str, Any]]:
        """여러 검색 결과 리스트를 RRF 알고리즘으로 융합합니다."""
        rrf_scores = {}
        
        # 각 결과 리스트에 대해 RRF 점수 계산
        for results, weight in zip(results_lists, weights):
            for rank, point in enumerate(results):
                point_id = point.id
                if point_id not in rrf_scores:
                    rrf_scores[point_id] = {'score': 0, 'payload': point.payload}
                
                # RRF 점수 추가 (가중치 적용)
                rrf_scores[point_id]['score'] += weight * (1 / (k + rank + 1))

        # 총점이 높은 순으로 정렬
        sorted_results = sorted(rrf_scores.items(), key=lambda item: item[1]['score'], reverse=True)
        
        # Qdrant 검색 결과와 유사한 형식으로 변환
        final_results = [
            {'id': pid, 'score': data['score'], 'payload': data['payload']}
            for pid, data in sorted_results
        ]
        return final_results

    def delete_vectors(self, ids: List[Union[str, int]]) -> bool:
        """ID 리스트를 받아 벡터 삭제"""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=ids)
            )
            logger.info(f"컬렉션 '{self.collection_name}'에서 {len(ids)}개 포인트 삭제 완료")
            return True
        except Exception as e:
            logger.error(f"벡터 삭제 오류: {e}")
            return False
            
    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        """현재 컬렉션의 정보 조회"""
        try:
            info = self.client.get_collection(self.collection_name)
            # models.CollectionInfo 객체를 딕셔너리로 변환하여 반환
            return info.model_dump()
        except Exception as e:
            logger.error(f"컬렉션 정보 조회 오류: {e}")
            return None
    
    def check_user_profile_exists(self, user_id: int, source: str = 'user_profile') -> bool:
        """특정 user_id의 프로필이 이미 Qdrant에 존재하는지 확인"""
        try:
            # user_id와 source로 필터링하여 검색
            result = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id)
                        ),
                        models.FieldCondition(
                            key="source",
                            match=models.MatchValue(value=source)
                        )
                    ]
                ),
                limit=1,
                with_payload=True
            )
            # 결과가 있으면 True, 없으면 False
            points, _ = result
            return len(points) > 0
        except Exception as e:
            logger.error(f"프로필 존재 여부 확인 오류: {e}")
            return False
    
    def delete_user_profile(self, user_id: int, source: str = 'user_profile') -> bool:
        """특정 user_id의 프로필을 Qdrant에서 삭제"""
        try:
            # user_id와 source로 필터링하여 삭제
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="user_id",
                                match=models.MatchValue(value=user_id)
                            ),
                            models.FieldCondition(
                                key="source",
                                match=models.MatchValue(value=source)
                            )
                        ]
                    )
                )
            )
            logger.info(f"사용자 {user_id}의 프로필이 Qdrant에서 삭제되었습니다.")
            return True
        except Exception as e:
            logger.error(f"프로필 삭제 오류: {e}")
            return False