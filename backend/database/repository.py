import os
import yaml
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
import logging
from .qdrant_client import QdrantManager
from .sqlite import SQLite
from pathlib import Path
from utils.path_utils import get_config_path

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
    
    def __init__(
        self,
        config_path: str = "configs.yaml",
        sqlite_instance: Optional["SQLite"] = None,
    ):
        config_file_path = get_config_path(config_path)
        self.config = self._load_config(config_file_path)
        self.qdrant = QdrantManager(config_path)
        configured_path = self.config.get('sqlite', {}).get('path')
        if configured_path and configured_path not in ("sqlite.db", "db/master.db"):
            logger.warning(
                "sqlite.path 설정은 더 이상 사용되지 않습니다. 프로젝트 루트의 db/ 디렉터리를 사용합니다."
            )
        self.sqlite = sqlite_instance or SQLite()
    
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
                    snippet=payload.get('snippet') or payload.get('content', ''),
                    content=payload.get('content')
                )
                hits.append(hit)
            
            return hits[:limit]
            
        except Exception as e:
            logger.error(f"하이브리드 검색 오류: {e}")
            return []
    
    def resolve_metadata(self, hit: Hit, user_id: int = None) -> Hit:
        """SQLite에서 메타데이터를 조합하여 Hit 완성"""
        try:
            if hit.source == 'file' and user_id:
                file_info = self.sqlite.get_file(user_id, hit.doc_id)
                if file_info:
                    hit.path = file_info.get('file_path', hit.path)
                    if not hit.snippet:
                        hit.snippet = ''
            
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
    def upsert_file(self, doc_id: str, user_id: int, file_path: str, **kwargs) -> bool:
        return self.sqlite.upsert_file(doc_id, user_id, file_path, **kwargs)
    
    def upsert_interest(self, user_id: int, keyword: str, score: float = 0.5, source: str = 'manual') -> bool:
        return self.sqlite.upsert_interest(user_id, keyword, score, source)
    
    def get_file(self, user_id: int, doc_id: str):
        return self.sqlite.get_file(user_id, doc_id)
    
    def get_user_interests(self, user_id: int, limit: int = 20):
        return self.sqlite.get_user_interests(user_id, limit)
    
    def find_file_by_path(self, user_id: int, path: str):
        return self.sqlite.find_file_by_path(user_id, path)
    
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
    
    # === 대시보드 관련 메서드들 ===
    
    def create_note(self, user_id: int, content: str, title: str = "", 
                    pinned: bool = False, tags: str = None):
        """새 노트 생성"""
        return self.sqlite.create_note(user_id, content, title, pinned, tags)
    
    def update_note(self, user_id: int, note_id: int, content: str = None,
                    title: str = None, pinned: bool = None, tags: str = None):
        """노트 업데이트"""
        return self.sqlite.update_note(user_id, note_id, content, title, pinned, tags)
    
    def delete_note(self, user_id: int, note_id: int):
        """노트 삭제"""
        return self.sqlite.delete_note(user_id, note_id)
    
    def get_notes(self, user_id: int, limit: int = 50):
        """노트 목록 조회"""
        return self.sqlite.get_notes(user_id, limit)
    
    def get_note_by_id(self, user_id: int, note_id: int):
        """특정 노트 조회"""
        return self.sqlite.get_note_by_id(user_id, note_id)
    
    def get_interest_trend(self, user_id: int, days: int = 30, keyword: str = None):
        """관심사 트렌드 조회"""
        return self.sqlite.get_interest_trend(user_id, days, keyword)
    
    def get_interest_summary(self, user_id: int, days: int = 30):
        """관심사 요약 통계"""
        return self.sqlite.get_interest_summary(user_id, days)
    
    def get_activity_summary(self, user_id: int, days: int = 7):
        """사용자 활동 요약"""
        return self.sqlite.get_activity_summary(user_id, days)
    
    def record_interest_snapshot(self, user_id: int):
        """관심사 스냅샷 기록"""
        return self.sqlite.record_interest_snapshot(user_id)
