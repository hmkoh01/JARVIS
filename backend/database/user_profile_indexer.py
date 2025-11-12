#!/usr/bin/env python3
"""
User Profile Indexer
사용자 설문 데이터를 Qdrant에 인덱싱하여 RAG 시스템에서 활용
"""

import os
import sys
from typing import Optional

# Add the backend directory to Python path
backend_path = os.path.join(os.path.dirname(__file__), '..')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from database.sqlite_meta import SQLiteMeta
from database.repository import Repository
from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder

_global_profile_indexer: Optional["UserProfileIndexer"] = None


def set_global_profile_indexer(indexer: "UserProfileIndexer") -> None:
    """전역 UserProfileIndexer 인스턴스를 등록합니다."""
    global _global_profile_indexer
    _global_profile_indexer = indexer


def get_global_profile_indexer() -> "UserProfileIndexer":
    """등록된 전역 UserProfileIndexer 인스턴스를 반환합니다."""
    if _global_profile_indexer is None:
        raise RuntimeError("전역 UserProfileIndexer 인스턴스가 초기화되지 않았습니다.")
    return _global_profile_indexer


class UserProfileIndexer:
    def __init__(self, repository: Repository, embedder: BGEM3Embedder):
        """
        싱글톤 패턴으로 관리되는 UserProfileIndexer.
        Repository와 BGEM3Embedder를 주입받아 사용합니다.
        """
        self.sqlite_meta = SQLiteMeta()
        self.repository = repository  # 주입받은 싱글톤 인스턴스 사용
        self.embedder = embedder      # 주입받은 싱글톤 인스턴스 사용
    
    def convert_survey_to_text(self, survey_data: dict) -> str:
        """설문 데이터를 자연어 텍스트로 변환합니다."""
        if not survey_data:
            return ""
        
        text_parts = []
        
        # 직업 분야
        job_field = survey_data.get('job_field', '')
        job_field_other = survey_data.get('job_field_other', '')
        if job_field == 'other' and job_field_other:
            text_parts.append(f"직업: {job_field_other}")
        elif job_field:
            job_mapping = {
                'student': '학생',
                'developer': '개발자/엔지니어',
                'designer': '디자이너',
                'planner': '기획자/마케터',
                'researcher': '연구원/교육자'
            }
            text_parts.append(f"직업: {job_mapping.get(job_field, job_field)}")
        
        # 관심 주제
        interests = survey_data.get('interests', [])
        if interests:
            interest_mapping = {
                'tech': 'IT/최신 기술',
                'finance': '경제/금융/투자',
                'ai': '인공지능/데이터 과학',
                'design': '디자인/예술',
                'marketing': '마케팅/비즈니스',
                'productivity': '생산성/자기계발',
                'health': '건강/운동',
                'travel': '여행/문화'
            }
            interest_texts = [interest_mapping.get(interest, interest) for interest in interests]
            text_parts.append(f"관심 주제: {', '.join(interest_texts)}")
        
        # 도움 받고 싶은 영역
        help_preferences = survey_data.get('help_preferences', [])
        if help_preferences:
            help_mapping = {
                'work_search': '업무 관련 정보 검색 및 요약',
                'inspiration': '새로운 아이디어나 영감 얻기',
                'writing': '글쓰기 보조',
                'learning': '개인적인 학습 및 지식 확장'
            }
            help_texts = [help_mapping.get(help, help) for help in help_preferences]
            text_parts.append(f"도움 받고 싶은 영역: {', '.join(help_texts)}")
        
        # 사용자 정의 키워드
        custom_keywords = survey_data.get('custom_keywords', '')
        if custom_keywords:
            text_parts.append(f"특별 관심 키워드: {custom_keywords}")
        
        return " | ".join(text_parts)
    
    def index_user_profile(self, user_id: int) -> bool:
        """사용자 프로필을 Qdrant에 인덱싱합니다."""
        try:
            import json
            
            # 중복 체크: 이미 프로필이 존재하는지 확인
            if self.repository.qdrant.check_user_profile_exists(user_id):
                print(f"사용자 {user_id}의 프로필이 이미 존재합니다. 인덱싱을 건너뜁니다.")
                return True
            
            # 설문 데이터 조회
            survey_data = self.sqlite_meta.get_user_survey_response(user_id)
            if not survey_data:
                print(f"사용자 {user_id}의 설문 데이터를 찾을 수 없습니다.")
                return False
            
            # 타입 체크: 문자열이면 JSON 파싱
            if isinstance(survey_data, str):
                survey_data = json.loads(survey_data)
            
            # 텍스트로 변환
            profile_text = self.convert_survey_to_text(survey_data)
            if not profile_text:
                print("변환된 프로필 텍스트가 비어있습니다.")
                return False
            
            # 임베딩 생성
            result = self.embedder.encode_single_query(profile_text)
            dense_vector = result['dense_vec']
            sparse_vector = result['sparse_vec']
            sparse_qdrant = self.embedder.convert_sparse_to_qdrant_format(sparse_vector)
            
            payload = {
                'user_id': user_id,
                'source': 'user_profile',
                'content': profile_text,
                'snippet': profile_text,  # snippet 필드 추가 (검색 결과에서 사용)
                'metadata': survey_data
            }
            
            self.repository.qdrant.upsert_vectors(
                dense_vectors=[dense_vector],
                sparse_vectors=[sparse_qdrant],
                payloads=[payload]
            )
            
            print(f"사용자 {user_id}의 프로필이 성공적으로 인덱싱되었습니다.")
            return True
            
        except Exception as e:
            print(f"프로필 인덱싱 오류: {e}")
            return False
    
    def update_user_profile(self, user_id: int) -> bool:
        """사용자 프로필을 업데이트합니다 (기존 프로필 삭제 후 재인덱싱)."""
        try:
            # 기존 프로필 삭제
            if self.repository.qdrant.check_user_profile_exists(user_id):
                print(f"사용자 {user_id}의 기존 프로필을 삭제합니다...")
                self.repository.qdrant.delete_user_profile(user_id)
            
            # 새 프로필 인덱싱 (중복 체크를 건너뛰기 위해 직접 구현)
            import json
            
            # 설문 데이터 조회
            survey_data = self.sqlite_meta.get_user_survey_response(user_id)
            if not survey_data:
                print(f"사용자 {user_id}의 설문 데이터를 찾을 수 없습니다.")
                return False
            
            # 타입 체크: 문자열이면 JSON 파싱
            if isinstance(survey_data, str):
                survey_data = json.loads(survey_data)
            
            # 텍스트로 변환
            profile_text = self.convert_survey_to_text(survey_data)
            if not profile_text:
                print("변환된 프로필 텍스트가 비어있습니다.")
                return False
            
            # 임베딩 생성
            result = self.embedder.encode_single_query(profile_text)
            dense_vector = result['dense_vec']
            sparse_vector = result['sparse_vec']
            sparse_qdrant = self.embedder.convert_sparse_to_qdrant_format(sparse_vector)
            
            payload = {
                'user_id': user_id,
                'source': 'user_profile',
                'content': profile_text,
                'snippet': profile_text,  # snippet 필드 추가 (검색 결과에서 사용)
                'metadata': survey_data
            }
            
            self.repository.qdrant.upsert_vectors(
                dense_vectors=[dense_vector],
                sparse_vectors=[sparse_qdrant],
                payloads=[payload]
            )
            
            print(f"사용자 {user_id}의 프로필이 성공적으로 업데이트되었습니다.")
            return True
            
        except Exception as e:
            print(f"프로필 업데이트 오류: {e}")
            return False
    
    def get_profile_as_context(self, user_id: int) -> Optional[str]:
        """사용자 프로필을 컨텍스트 형태로 반환합니다."""
        try:
            survey_data = self.sqlite_meta.get_user_survey_response(user_id)
            if not survey_data:
                return None
            
            profile_text = self.convert_survey_to_text(survey_data)
            return f"사용자 프로필 정보: {profile_text}"
            
        except Exception as e:
            print(f"프로필 컨텍스트 조회 오류: {e}")
            return None