#sqlite.py
"""
SQLite 데이터베이스 관리 모듈 (Refactored for Keyword-Centric Recommendation)
- 불필요한 테이블 제거: app_logs
- 간소화된 테이블: browser_logs (id, url, title, visit_time), files (doc_id, file_path, processed_at)
- 새로운 핵심 테이블: content_keywords (KeyBERT 등 키워드 추출 결과 저장)
"""
import sqlite3
import os
import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging
import threading

logger = logging.getLogger(__name__)


class SQLite:
    """SQLite 데이터베이스 관리 클래스 (Keyword-Centric Architecture)"""
    
    def _sanitize_path(self, path: str) -> str:
        """경로에서 유효하지 않은 문자들을 '_'로 대체하여 안전하게 만듭니다."""
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        sanitized_filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        if directory:
            return os.path.join(directory, sanitized_filename)
        return sanitized_filename

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            backend_dir = Path(__file__).resolve().parents[1]
            db_path = backend_dir / "sqlite" / "sqlite.db"
        self.db_path = self._sanitize_path(str(db_path))
        self._ensure_db_dir()
        self._thread_local = threading.local()
        self._init_db()
    
    @property
    def conn(self) -> sqlite3.Connection:
        """현재 스레드에 맞는 DB 연결을 가져오거나 새로 생성합니다."""
        if not hasattr(self._thread_local, 'conn') or self._thread_local.conn is None:
            try:
                connection = sqlite3.connect(self.db_path)
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.row_factory = sqlite3.Row
                self._thread_local.conn = connection
            except Exception as e:
                logger.error(f"SQLite 연결 생성 오류: {e}")
                return None
        return self._thread_local.conn

    def close_connection(self):
        if hasattr(self._thread_local, 'conn') and self._thread_local.conn:
            self._thread_local.conn.close()
            self._thread_local.conn = None
    
    def __del__(self):
        """소멸자에서 연결 정리"""
        self.close_connection()
    
    def _ensure_db_dir(self):
        """데이터베이스 디렉토리 생성"""
        if os.path.dirname(self.db_path):
            db_dir = os.path.dirname(self.db_path)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
    
    def _init_db(self):
        """데이터베이스 초기화 및 테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            # ============================================================
            # 1. Users & Profile
            # ============================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    google_user_id TEXT UNIQUE,
                    refresh_token TEXT,
                    selected_root_folder TEXT,
                    has_completed_setup BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_interests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    score REAL DEFAULT 0.5,
                    source TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_survey_responses (
                    user_id INTEGER PRIMARY KEY,
                    response_json TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # ============================================================
            # 2. Data Logs (Simplified)
            # ============================================================
            # browser_logs: 세션 분석용 최소 정보만 저장
            conn.execute("""
                CREATE TABLE IF NOT EXISTS browser_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT,
                    title TEXT,
                    visit_time DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # ============================================================
            # 3. Files (Simplified)
            # ============================================================
            # files: 핵심 메타데이터만 저장 (키워드는 content_keywords에서 관리)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    doc_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # ============================================================
            # 4. Content Keywords (NEW - Core Table for Recommendations)
            # ============================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    original_text TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # ============================================================
            # 5. Recommendations (Active Agent Core)
            # ============================================================
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recommendation_blacklist (
                    user_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, keyword)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    trigger_type TEXT,
                    keyword TEXT,
                    related_keywords TEXT,
                    bubble_message TEXT,
                    report_content TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    responded_at DATETIME,
                    generated_report_file_id TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            """)
            
            # ============================================================
            # Indexes
            # ============================================================
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_interests_user_id ON user_interests(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_logs_user_id ON browser_logs(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_logs_visit_time ON browser_logs(visit_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_user_id_status ON recommendations(user_id, status)")
            # content_keywords indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_keywords_user_id ON content_keywords(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_keywords_created_at ON content_keywords(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_keywords_source ON content_keywords(source_type, source_id)")
            
            conn.commit()

    # ============================================================
    # User Management Methods
    # ============================================================
    
    def get_or_create_user_by_google(self, google_id: str, email: str, refresh_token: str = None) -> Optional[Dict[str, Any]]:
        """Google ID로 사용자를 조회하고, 없으면 새로 생성 후 user 객체를 반환"""
        try:
            self.conn.row_factory = sqlite3.Row
            
            # 기존 사용자 조회
            cursor = self.conn.execute(
                "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
            )
            row = cursor.fetchone()
            
            if row:
                # 사용자 존재 - refresh_token 업데이트 (제공된 경우)
                if refresh_token:
                    self.conn.execute(
                        "UPDATE users SET refresh_token = ? WHERE google_user_id = ?",
                        (refresh_token, google_id)
                    )
                    self.conn.commit()
                    cursor = self.conn.execute(
                        "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
                    )
                    row = cursor.fetchone()
                return dict(row) if row else None
            else:
                # 새 사용자 생성
                cursor = self.conn.execute("""
                    INSERT INTO users (google_user_id, email, refresh_token, has_completed_setup)
                    VALUES (?, ?, ?, 0)
                """, (google_id, email, refresh_token))
                self.conn.commit()
                
                cursor = self.conn.execute(
                    "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"사용자 조회/생성 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """user_id로 사용자 정보 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"사용자 조회 오류: {e}")
            return None
    
    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        """google_user_id로 사용자 정보 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute(
                "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"사용자 조회 오류: {e}")
            return None
    
    def update_user_setup_status(self, user_id: int, status: int) -> bool:
        """has_completed_setup 값을 업데이트"""
        try:
            self.conn.execute(
                "UPDATE users SET has_completed_setup = ? WHERE user_id = ?",
                (status, user_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"사용자 설정 상태 업데이트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def update_user_folder(self, user_id: int, folder_path: str) -> bool:
        """selected_root_folder 경로를 업데이트"""
        try:
            self.conn.execute(
                "UPDATE users SET selected_root_folder = ? WHERE user_id = ?",
                (folder_path, user_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"사용자 폴더 경로 업데이트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_user_folder(self, user_id: int) -> Optional[str]:
        """user_id로 selected_root_folder 경로를 조회"""
        try:
            cursor = self.conn.execute(
                "SELECT selected_root_folder FROM users WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception as e:
            logger.error(f"사용자 폴더 경로 조회 오류: {e}")
            return None
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """모든 사용자 목록을 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("SELECT user_id, email FROM users")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"모든 사용자 조회 오류: {e}")
            return []

    # ============================================================
    # User Interests Methods
    # ============================================================
    
    def upsert_interest(self, user_id: int, keyword: str, score: float = 0.5, source: str = 'manual') -> bool:
        """관심사 업서트"""
        try:
            # 기존 관심사 조회
            cursor = self.conn.execute(
                "SELECT id, score FROM user_interests WHERE user_id = ? AND keyword = ?",
                (user_id, keyword)
            )
            row = cursor.fetchone()
            
            if row:
                # 기존 관심사 업데이트 (점수 가중 평균)
                new_score = min(1.0, (row[1] + score) / 2 + 0.1)
                self.conn.execute("""
                    UPDATE user_interests 
                    SET score = ?, last_detected_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_score, row[0]))
            else:
                # 새 관심사 추가
                self.conn.execute("""
                    INSERT INTO user_interests (user_id, keyword, score, source)
                    VALUES (?, ?, ?, ?)
                """, (user_id, keyword, score, source))
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"관심사 업서트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_user_interests(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자 관심사 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM user_interests 
                WHERE user_id = ? 
                ORDER BY score DESC, last_detected_at DESC 
                LIMIT ?
            """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"관심사 조회 오류: {e}")
            return []
    
    def delete_interest(self, user_id: int, keyword: str) -> bool:
        """관심사 삭제"""
        try:
            self.conn.execute(
                "DELETE FROM user_interests WHERE user_id = ? AND keyword = ?",
                (user_id, keyword)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"관심사 삭제 오류: {e}")
            return False

    # ============================================================
    # Survey Response Methods
    # ============================================================
    
    def upsert_survey_response(self, user_id: int, response_data: Dict[str, Any]) -> bool:
        """설문지 응답 저장 (업서트)"""
        try:
            response_json = json.dumps(response_data, ensure_ascii=False)
            self.conn.execute("""
                INSERT INTO user_survey_responses (user_id, response_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, response_json))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"설문지 응답 저장 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_survey_response(self, user_id: int) -> Optional[Dict[str, Any]]:
        """사용자 설문지 응답 조회"""
        try:
            cursor = self.conn.execute(
                "SELECT response_json FROM user_survey_responses WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None
        except Exception as e:
            logger.error(f"설문지 응답 조회 오류: {e}")
            return None
    
    def has_user_completed_survey(self, user_id: int) -> bool:
        """사용자가 설문지를 완료했는지 확인"""
        try:
            cursor = self.conn.execute(
                "SELECT 1 FROM user_survey_responses WHERE user_id = ? LIMIT 1",
                (user_id,)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"설문지 완료 여부 확인 오류: {e}")
            return False

    # ============================================================
    # Browser Logs Methods (Simplified)
    # ============================================================
    
    def insert_browser_log(self, user_id: int, url: str, title: str = None, 
                           visit_time: datetime = None) -> Optional[int]:
        """브라우저 로그 삽입 (간소화: id, url, title, visit_time만 저장)"""
        try:
            if visit_time is None:
                visit_time = datetime.now()
            
            cursor = self.conn.execute("""
                INSERT INTO browser_logs (user_id, url, title, visit_time)
                VALUES (?, ?, ?, ?)
            """, (user_id, url, title, visit_time))
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"브라우저 로그 삽입 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return None
    
    def get_browser_logs(self, user_id: int, limit: int = 100, 
                         since: datetime = None) -> List[Dict[str, Any]]:
        """브라우저 로그 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            if since:
                cursor = self.conn.execute("""
                    SELECT * FROM browser_logs 
                    WHERE user_id = ? AND visit_time >= ?
                    ORDER BY visit_time DESC LIMIT ?
                """, (user_id, since, limit))
            else:
                cursor = self.conn.execute("""
                    SELECT * FROM browser_logs 
                    WHERE user_id = ?
                    ORDER BY visit_time DESC LIMIT ?
                """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"브라우저 로그 조회 오류: {e}")
            return []
    
    def is_browser_log_duplicate(self, user_id: int, url: str, visit_time: datetime) -> bool:
        """브라우저 로그 중복 확인"""
        try:
            cursor = self.conn.execute("""
                SELECT 1 FROM browser_logs 
                WHERE user_id = ? AND url = ? AND visit_time = ? 
                LIMIT 1
            """, (user_id, url, visit_time))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"브라우저 로그 중복 체크 오류: {e}")
            return False

    # ============================================================
    # Files Methods (Simplified)
    # ============================================================
    
    def upsert_file(self, doc_id: str, user_id: int, file_path: str) -> bool:
        """파일 정보 업서트 (간소화: doc_id, user_id, file_path, processed_at만 저장)"""
        try:
            self.conn.execute("""
                INSERT INTO files (doc_id, user_id, file_path, processed_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(doc_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    processed_at = CURRENT_TIMESTAMP
            """, (doc_id, user_id, file_path))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"파일 업서트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_file(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """파일 정보 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("SELECT * FROM files WHERE doc_id = ?", (doc_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"파일 조회 오류: {e}")
            return None
    
    def find_file_by_path(self, file_path: str) -> Optional[str]:
        """경로로 파일 doc_id 찾기"""
        try:
            cursor = self.conn.execute(
                "SELECT doc_id FROM files WHERE file_path = ?", (file_path,)
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"파일 경로 조회 오류: {e}")
            return None
    
    def is_file_exists(self, doc_id: str) -> bool:
        """파일이 이미 존재하는지 확인"""
        try:
            cursor = self.conn.execute(
                "SELECT 1 FROM files WHERE doc_id = ? LIMIT 1",
                (doc_id,)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"파일 존재 확인 오류: {e}")
            return False
    
    def get_file_last_modified(self, file_path: str) -> Optional[datetime]:
        """파일의 마지막 처리 시간 조회"""
        try:
            cursor = self.conn.execute(
                "SELECT processed_at FROM files WHERE file_path = ? ORDER BY processed_at DESC LIMIT 1",
                (file_path,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None
        except Exception as e:
            logger.error(f"파일 수정 시간 조회 오류: {e}")
            return None
    
    def get_user_files(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """사용자 파일 목록 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM files 
                WHERE user_id = ? 
                ORDER BY processed_at DESC 
                LIMIT ?
            """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"파일 조회 오류: {e}")
            return []

    # ============================================================
    # Content Keywords Methods (NEW - Core for Recommendations)
    # ============================================================
    
    def insert_content_keyword(self, user_id: int, source_type: str, source_id: str,
                                keyword: str, original_text: str = None) -> Optional[int]:
        """콘텐츠 키워드 삽입
        
        Args:
            user_id: 사용자 ID
            source_type: 'file', 'web', 'chat' 중 하나
            source_id: files.doc_id, browser_logs.id, 또는 chat_session_id
            keyword: 추출된 핵심 키워드
            original_text: 문맥 파악을 위한 짧은 원문 조각 (선택)
        
        Returns:
            삽입된 키워드의 ID, 실패 시 None
        """
        try:
            cursor = self.conn.execute("""
                INSERT INTO content_keywords (user_id, source_type, source_id, keyword, original_text)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, source_type, source_id, keyword, original_text))
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"콘텐츠 키워드 삽입 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return None
    
    def insert_content_keywords_batch(self, keywords: List[Dict[str, Any]]) -> int:
        """콘텐츠 키워드 일괄 삽입
        
        Args:
            keywords: 키워드 딕셔너리 리스트
                각 항목: {'user_id', 'source_type', 'source_id', 'keyword', 'original_text'(선택)}
        
        Returns:
            삽입된 키워드 수
        """
        if not keywords:
            return 0
        
        try:
            inserted = 0
            self.conn.execute("BEGIN TRANSACTION")
            for kw in keywords:
                self.conn.execute("""
                    INSERT INTO content_keywords (user_id, source_type, source_id, keyword, original_text)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    kw['user_id'],
                    kw['source_type'],
                    kw['source_id'],
                    kw['keyword'],
                    kw.get('original_text')
                ))
                inserted += 1
            self.conn.commit()
            return inserted
        except Exception as e:
            logger.error(f"콘텐츠 키워드 일괄 삽입 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return 0
    
    def get_content_keywords(self, user_id: int, source_type: str = None,
                              limit: int = 100, since: datetime = None) -> List[Dict[str, Any]]:
        """콘텐츠 키워드 조회
        
        Args:
            user_id: 사용자 ID
            source_type: 필터링할 소스 타입 ('file', 'web', 'chat'), None이면 전체
            limit: 최대 반환 개수
            since: 이 시간 이후의 키워드만 조회
        
        Returns:
            키워드 딕셔너리 리스트
        """
        try:
            self.conn.row_factory = sqlite3.Row
            query = "SELECT * FROM content_keywords WHERE user_id = ?"
            params = [user_id]
            
            if source_type:
                query += " AND source_type = ?"
                params.append(source_type)
            
            if since:
                query += " AND created_at >= ?"
                params.append(since)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = self.conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"콘텐츠 키워드 조회 오류: {e}")
            return []
    
    def get_keywords_by_source(self, source_type: str, source_id: str) -> List[Dict[str, Any]]:
        """특정 소스의 키워드 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM content_keywords 
                WHERE source_type = ? AND source_id = ?
                ORDER BY created_at DESC
            """, (source_type, source_id))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"소스별 키워드 조회 오류: {e}")
            return []
    
    def get_keyword_frequency(self, user_id: int, limit: int = 50,
                               since: datetime = None) -> List[Dict[str, Any]]:
        """키워드 빈도 분석 (추천 시스템용)
        
        Returns:
            [{'keyword': str, 'count': int, 'sources': int}, ...]
        """
        try:
            query = """
                SELECT keyword, COUNT(*) as count, COUNT(DISTINCT source_id) as sources
                FROM content_keywords 
                WHERE user_id = ?
            """
            params = [user_id]
            
            if since:
                query += " AND created_at >= ?"
                params.append(since)
            
            query += " GROUP BY keyword ORDER BY count DESC LIMIT ?"
            params.append(limit)
            
            cursor = self.conn.execute(query, params)
            return [{'keyword': row[0], 'count': row[1], 'sources': row[2]} 
                    for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"키워드 빈도 분석 오류: {e}")
            return []
    
    def delete_keywords_by_source(self, source_type: str, source_id: str) -> bool:
        """특정 소스의 키워드 삭제 (재처리 시 사용)"""
        try:
            self.conn.execute(
                "DELETE FROM content_keywords WHERE source_type = ? AND source_id = ?",
                (source_type, source_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"키워드 삭제 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False

    # ============================================================
    # Recommendation Blacklist Methods
    # ============================================================
    
    def add_to_blacklist(self, user_id: int, keyword: str) -> bool:
        """키워드를 블랙리스트에 추가"""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO recommendation_blacklist (user_id, keyword)
                VALUES (?, ?)
            """, (user_id, keyword))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"블랙리스트 추가 오류: {e}")
            return False
    
    def remove_from_blacklist(self, user_id: int, keyword: str) -> bool:
        """키워드를 블랙리스트에서 제거"""
        try:
            self.conn.execute(
                "DELETE FROM recommendation_blacklist WHERE user_id = ? AND keyword = ?",
                (user_id, keyword)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"블랙리스트 제거 오류: {e}")
            return False
    
    def get_blacklist(self, user_id: int) -> List[str]:
        """사용자의 블랙리스트 조회"""
        try:
            cursor = self.conn.execute(
                "SELECT keyword FROM recommendation_blacklist WHERE user_id = ?",
                (user_id,)
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"블랙리스트 조회 오류: {e}")
            return []
    
    def is_keyword_blacklisted(self, user_id: int, keyword: str) -> bool:
        """키워드가 블랙리스트에 있는지 확인"""
        try:
            cursor = self.conn.execute(
                "SELECT 1 FROM recommendation_blacklist WHERE user_id = ? AND keyword = ? LIMIT 1",
                (user_id, keyword)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"블랙리스트 확인 오류: {e}")
            return False

    # ============================================================
    # Recommendations Methods
    # ============================================================
    
    def create_recommendation(self, user_id: int, trigger_type: str, keyword: str,
                              bubble_message: str, related_keywords: List[str] = None,
                              report_content: str = None) -> int:
        """새로운 추천 생성"""
        try:
            related_json = json.dumps(related_keywords or [], ensure_ascii=False)
            cursor = self.conn.execute("""
                INSERT INTO recommendations 
                (user_id, trigger_type, keyword, related_keywords, bubble_message, report_content, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (user_id, trigger_type, keyword, related_json, bubble_message, report_content))
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"추천 생성 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return -1
    
    def get_recommendation(self, recommendation_id: int) -> Optional[Dict[str, Any]]:
        """추천 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute(
                "SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)
            )
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get('related_keywords'):
                    result['related_keywords'] = json.loads(result['related_keywords'])
                return result
            return None
        except Exception as e:
            logger.error(f"추천 조회 오류: {e}")
            return None
    
    def get_pending_recommendations(self, user_id: int) -> List[Dict[str, Any]]:
        """대기 중인 추천 목록 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM recommendations 
                WHERE user_id = ? AND status = 'pending'
                ORDER BY created_at DESC
            """, (user_id,))
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get('related_keywords'):
                    item['related_keywords'] = json.loads(item['related_keywords'])
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"대기 중 추천 조회 오류: {e}")
            return []
    
    def get_all_recommendations(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """사용자의 모든 추천 내역 조회"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM recommendations 
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit))
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get('related_keywords'):
                    item['related_keywords'] = json.loads(item['related_keywords'])
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"모든 추천 조회 오류: {e}")
            return []
    
    def update_recommendation_status(self, recommendation_id: int, status: str) -> bool:
        """추천 상태 업데이트 (pending -> shown -> accepted/rejected -> completed)"""
        try:
            self.conn.execute("""
                UPDATE recommendations 
                SET status = ?, responded_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (status, recommendation_id))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"추천 상태 업데이트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def update_recommendation_report(self, recommendation_id: int, 
                                      report_content: str, report_file_id: str = None) -> bool:
        """추천 보고서 업데이트"""
        try:
            self.conn.execute("""
                UPDATE recommendations 
                SET report_content = ?, generated_report_file_id = ?
                WHERE id = ?
            """, (report_content, report_file_id, recommendation_id))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"추천 보고서 업데이트 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_recent_recommendation_count(self, user_id: int, trigger_type: str = None, 
                                         hours: int = 1) -> int:
        """지정된 시간 내에 생성된 추천 개수 조회"""
        try:
            since = datetime.now() - timedelta(hours=hours)
            if trigger_type:
                cursor = self.conn.execute("""
                    SELECT COUNT(*) FROM recommendations
                    WHERE user_id = ? AND trigger_type = ? AND created_at >= ?
                """, (user_id, trigger_type, since))
            else:
                cursor = self.conn.execute("""
                    SELECT COUNT(*) FROM recommendations
                    WHERE user_id = ? AND created_at >= ?
                """, (user_id, since))
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"최근 추천 횟수 조회 오류: {e}")
            return 0

    # ============================================================
    # Statistics Methods
    # ============================================================
    
    def get_collection_stats(self, user_id: int = None) -> Dict[str, int]:
        """데이터 수집 통계 조회"""
        try:
            stats = {}
            if user_id:
                cursor = self.conn.execute(
                    "SELECT COUNT(*) FROM files WHERE user_id = ?", (user_id,)
                )
                stats['files'] = cursor.fetchone()[0]
                cursor = self.conn.execute(
                    "SELECT COUNT(*) FROM browser_logs WHERE user_id = ?", (user_id,)
                )
                stats['browser_logs'] = cursor.fetchone()[0]
                cursor = self.conn.execute(
                    "SELECT COUNT(*) FROM content_keywords WHERE user_id = ?", (user_id,)
                )
                stats['content_keywords'] = cursor.fetchone()[0]
            else:
                cursor = self.conn.execute("SELECT COUNT(*) FROM files")
                stats['files'] = cursor.fetchone()[0]
                cursor = self.conn.execute("SELECT COUNT(*) FROM browser_logs")
                stats['browser_logs'] = cursor.fetchone()[0]
                cursor = self.conn.execute("SELECT COUNT(*) FROM content_keywords")
                stats['content_keywords'] = cursor.fetchone()[0]
            return stats
        except Exception as e:
            logger.error(f"수집 통계 조회 오류: {e}")
            return {}

    # ============================================================
    # Data Cleanup Methods
    # ============================================================
    
    def delete_user_data(self, user_id: int) -> bool:
        """사용자 관련 모든 데이터 삭제"""
        try:
            tables = [
                'recommendations', 'recommendation_blacklist', 
                'browser_logs', 'files', 'content_keywords',
                'user_interests', 'user_survey_responses'
            ]
            for table in tables:
                self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            self.conn.commit()
            logger.info(f"사용자 {user_id}의 모든 데이터가 삭제되었습니다.")
            return True
        except Exception as e:
            logger.error(f"사용자 데이터 삭제 오류: {e}")
            if self.conn:
                self.conn.rollback()
            return False

    # ============================================================
    # Legacy Compatibility Methods (for data_collector.py)
    # ============================================================
    
    def get_last_browser_collection_time(self, user_id: int, browser_name: str = None) -> Optional[datetime]:
        """마지막 브라우저 히스토리 수집 시간 조회"""
        try:
            cursor = self.conn.execute("""
                SELECT MAX(visit_time) FROM browser_logs WHERE user_id = ?
            """, (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0]) if isinstance(row[0], str) else row[0]
            return None
        except Exception as e:
            logger.error(f"마지막 브라우저 수집 시간 조회 오류: {e}")
            return None
    
    def insert_collected_file(self, file_info: Dict[str, Any]) -> bool:
        """수집된 파일 정보 저장 (Legacy 호환 - 간소화)"""
        try:
            import hashlib
            file_path = file_info['file_path']
            doc_id = f"file_{hashlib.md5(file_path.encode()).hexdigest()}"
            return self.upsert_file(
                doc_id=doc_id,
                user_id=file_info['user_id'],
                file_path=file_path
            )
        except Exception as e:
            logger.error(f"수집된 파일 저장 오류: {e}")
            return False
    
    def insert_collected_browser_history(self, history_info: Dict[str, Any]) -> Optional[int]:
        """수집된 브라우저 히스토리 저장 (Legacy 호환 - 간소화)"""
        return self.insert_browser_log(
            user_id=history_info['user_id'],
            url=history_info.get('url', ''),
            title=history_info.get('title'),
            visit_time=history_info.get('visit_time')
        )
    
    def get_user_survey_response(self, user_id: int) -> Optional[Dict[str, Any]]:
        """사용자 설문지 응답 조회 (Legacy 호환 - get_survey_response 래핑)"""
        return self.get_survey_response(user_id)
    
    def insert_survey_response(self, user_id: int, survey_data: Dict[str, Any]) -> bool:
        """설문지 응답 저장 (Legacy 호환 - upsert_survey_response 래핑)"""
        return self.upsert_survey_response(user_id, survey_data)
    
    def get_unread_recommendations(self, user_id: int) -> List[Dict[str, Any]]:
        """읽지 않은(pending) 추천 목록 조회 (Legacy 호환)"""
        return self.get_pending_recommendations(user_id)
    
    def mark_recommendation_as_read(self, recommendation_id: int) -> bool:
        """추천을 읽음으로 표시 (Legacy 호환 - status를 'shown'으로 변경)"""
        return self.update_recommendation_status(recommendation_id, 'shown')
    
    def insert_recommendation(self, user_id: int, title: str, content: str, 
                              recommendation_type: str = 'periodic') -> bool:
        """새로운 추천 저장 (Legacy 호환)"""
        try:
            rec_id = self.create_recommendation(
                user_id=user_id,
                trigger_type=recommendation_type,
                keyword=title,
                bubble_message=title,
                report_content=content
            )
            return rec_id > 0
        except Exception as e:
            logger.error(f"추천 저장 오류: {e}")
            return False
    
    def get_recent_manual_recommendation_count(self, user_id: int, hours: int = 1) -> int:
        """지정된 시간 내에 생성된 수동 추천 개수 (Legacy 호환)"""
        return self.get_recent_recommendation_count(user_id, trigger_type='manual', hours=hours)
    
    def get_collected_files(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """사용자 파일 목록 조회 (Legacy 호환)"""
        return self.get_user_files(user_id, limit)
    
    def get_collected_files_since(self, user_id: int, since: datetime) -> List[Dict[str, Any]]:
        """특정 시간 이후의 사용자 파일 목록 조회 (Legacy 호환)"""
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.execute("""
                SELECT * FROM files
                WHERE user_id = ? AND processed_at >= ?
                ORDER BY processed_at DESC
            """, (user_id, since))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"시간 기반 파일 조회 오류: {e}")
            return []
    
    def get_collected_browser_history(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """사용자 브라우저 히스토리 조회 (Legacy 호환)"""
        return self.get_browser_logs(user_id, limit)
    
    def get_collected_browser_history_since(self, user_id: int, since: datetime) -> List[Dict[str, Any]]:
        """특정 시간 이후의 브라우저 히스토리 조회 (Legacy 호환)"""
        return self.get_browser_logs(user_id, since=since)


# Backward compatibility alias
SQLiteMeta = SQLite
