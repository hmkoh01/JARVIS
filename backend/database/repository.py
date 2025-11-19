import os
import yaml
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
import logging
from .qdrant_client import QdrantManager
from .sqlite_meta import SQLiteMeta
from pathlib import Path
from utils.path_utils import get_config_path, get_db_path

PROJECT_ROOT = Path(__file__).parent.parent.parent
logger = logging.getLogger(__name__)

@dataclass
class Hit:
    """검색 결과 히트"""
    doc_id: str
    score: float
    source: str  # "file" | "web" | "screen"
    page: Optional[int] = None
    bbox: Optional[List[int]] = None
    path: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[int] = None
    snippet: Optional[str] = None
    content: Optional[str] = None

class Repository:
    """Qdrant + SQLite 통합 Repository (하이브리드 검색 지원)"""
    
    def __init__(self, config_path: str = "configs.yaml"):
        config_file_path = get_config_path(config_path)
        self.config = self._load_config(config_file_path)
        self.qdrant = QdrantManager(config_path)
        # EXE 환경 호환 DB 경로 사용
        db_path = get_db_path(self.config.get('sqlite', {}).get('path', 'meta.db'))
        self.sqlite = SQLiteMeta(str(db_path))
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """설정 파일 로드"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            else:
                return {
                    'qdrant': {
                        'url': 'http://localhost:6333',
                        'collection_name': 'user_context'
                    },
                    'sqlite': {
                        'path': 'meta.db'
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
                'sqlite': {
                    'path': 'meta.db'
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
    
    def index_documents_batch(self, 
                             doc_ids: List[str], 
                             dense_vectors: List[List[float]], 
                             sparse_vectors: List[Dict[str, List]],
                             metas: List[Dict[str, Any]]) -> bool:
        """문서 배치 인덱싱 (하이브리드)"""
        try:
            # 메타데이터에 doc_id 추가
            for i, meta in enumerate(metas):
                meta.update({
                    'doc_id': doc_ids[i]
                })
            
            return self.qdrant.upsert_vectors(metas, dense_vectors, sparse_vectors)
        except Exception as e:
            logger.error(f"문서 배치 인덱싱 오류: {e}")
            return False
    
    def search_hybrid(self, 
                     query_dense: List[float], 
                     query_sparse: Dict[str, List],
                     limit: int = 10,
                     source_filter: Optional[str] = None,
                     query_filter: Optional[Dict[str, Any]] = None) -> List[Hit]:
        """하이브리드 검색 (Dense + Sparse)"""
        try:
            # Qdrant 하이브리드 검색
            results = self.qdrant.hybrid_search(
                query_dense=query_dense,
                query_sparse=query_sparse,
                limit=limit,
                query_filter=query_filter
            )
            
            # 결과를 Hit 객체로 변환
            hits = []
            for result in results:
                payload = result.get('payload', {})
                
                # Source 필터 적용
                if source_filter and payload.get('source') != source_filter:
                    continue
                
                hit = Hit(
                    doc_id=payload.get('doc_id', ''),
                    score=result.get('score', 0.0),
                    source=payload.get('source', 'file'),
                    page=payload.get('page'),
                    path=payload.get('path'),
                    url=payload.get('url'),
                    timestamp=payload.get('timestamp'),
                    snippet=payload.get('snippet') or payload.get('content', ''),  # content도 fallback으로 사용
                    content=payload.get('content')
                )
                hits.append(hit)
            
            return hits[:limit]
            
        except Exception as e:
            logger.error(f"하이브리드 검색 오류: {e}")
            return []
    
    def resolve_metadata(self, hit: Hit) -> Hit:
        """SQLite에서 메타데이터를 조합하여 Hit 완성"""
        try:
            if hit.source == 'file':
                file_info = self.sqlite.get_file(hit.doc_id)
                if file_info:
                    hit.path = file_info.get('path', hit.path)
                    hit.timestamp = file_info.get('updated_at', hit.timestamp)
                    if not hit.snippet:
                        hit.snippet = file_info.get('preview', '')[:200]
            
            elif hit.source == 'web':
                # 웹 히스토리 정보 조회 (필요시)
                pass
            
            elif hit.source == 'screen':
                # 스크린샷 정보 조회 (필요시)
                pass
            
            return hit
            
        except Exception as e:
            logger.error(f"메타데이터 해석 오류: {e}")
            return hit
    
    # SQLite 메타데이터 메서드들 (위임)
    def upsert_file(self, doc_id: str, path: str, **kwargs) -> bool:
        return self.sqlite.upsert_file(doc_id, path, **kwargs)
    
    def insert_web_history(self, url: str, **kwargs) -> bool:
        return self.sqlite.insert_web_history(url, **kwargs)
    
    def insert_app(self, name: str, **kwargs) -> bool:
        return self.sqlite.insert_app(name, **kwargs)
    
    
    def upsert_interest(self, user_id: str, topic: str, score: float = 1.0) -> bool:
        return self.sqlite.upsert_interest(user_id, topic, score)
    
    def get_file(self, doc_id: str):
        return self.sqlite.get_file(doc_id)
    
    def recent_web_history(self, limit: int = 100, since_ts: int = None):
        return self.sqlite.recent_web_history(limit, since_ts)
    
    def recent_apps(self, limit: int = 100, since_ts: int = None):
        return self.sqlite.recent_apps(limit, since_ts)
    
    
    def top_interests(self, user_id: str, limit: int = 10):
        return self.sqlite.top_interests(user_id, limit)
    
    def find_file_by_path(self, path: str):
        return self.sqlite.find_file_by_path(path)
    
    # === 사용자 관리 메서드들 ===
    
    def get_or_create_user_by_google(self, google_id: str, email: str, refresh_token: str = None):
        """Google ID로 사용자를 조회하고, 없으면 새로 생성 후 user 객체를 반환"""
        return self.sqlite.get_or_create_user_by_google(google_id, email, refresh_token)
    
    def get_user_by_id(self, user_id: int):
        """user_id로 사용자 정보 조회"""
        return self.sqlite.get_user_by_id(user_id)
    
    def get_user_by_google_id(self, google_id: str):
        """google_user_id로 사용자 정보 조회"""
        return self.sqlite.get_user_by_google_id(google_id)
    
    def update_user_setup_status(self, user_id: int, status: int) -> bool:
        """has_completed_setup 값을 (0 또는 1로) 업데이트"""
        return self.sqlite.update_user_setup_status(user_id, status)
    
    def update_user_folder(self, user_id: int, folder_path: str) -> bool:
        """selected_root_folder 경로를 업데이트"""
        return self.sqlite.update_user_folder(user_id, folder_path)
    
    def get_user_folder(self, user_id: int):
        """user_id로 selected_root_folder 경로를 조회"""
        return self.sqlite.get_user_folder(user_id)
