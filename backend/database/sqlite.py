#sqlite.py
"""
SQLite 데이터베이스 관리 모듈 (User-Per-File Architecture)
- 물리적 데이터 격리: 각 사용자마다 별도의 DB 파일 (db/user_{user_id}.db)
- 중앙 마스터 DB: 사용자 인증 정보만 저장 (db/master.db)
- 마이그레이션: PRAGMA user_version 기반 스키마 버전 관리
"""
import sqlite3
import os
import re
import json
import glob
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging
import threading

logger = logging.getLogger(__name__)

# =============================================================================
# Schema Version & Migration Scripts
# =============================================================================
CURRENT_DB_VERSION = 1

# 마이그레이션 스크립트: version -> (description, [sql_statements])
# 버전은 해당 버전으로 업그레이드할 때 실행할 SQL들
MIGRATION_SCRIPTS: Dict[int, tuple] = {
    # Version 1: Initial schema (이 버전에서는 CREATE TABLE만 하므로 마이그레이션 불필요)
    # 향후 버전 추가 예시:
    # 2: ("Add new column to recommendations", [
    #     "ALTER TABLE recommendations ADD COLUMN priority INTEGER DEFAULT 0",
    # ]),
}


class SQLite:
    """SQLite 데이터베이스 관리 클래스 (User-Per-File Architecture)
    
    Architecture:
        - db/master.db: 중앙 사용자 테이블 (인증용)
        - db/user_{user_id}.db: 사용자별 데이터 파일
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """싱글톤 패턴 구현"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self._thread_local = threading.local()
        self._user_connections: Dict[int, threading.local] = {}
        self._ensure_db_dir()
        self._init_master_db()
        self._initialized = True
    
    # =========================================================================
    # Path Helpers
    # =========================================================================
    
    def _sanitize_path(self, path: str) -> str:
        """경로에서 유효하지 않은 문자들을 '_'로 대체"""
        directory = os.path.dirname(path)
        filename = os.path.basename(path)
        sanitized_filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        if directory:
            return os.path.join(directory, sanitized_filename)
        return sanitized_filename
    
    def _get_db_dir(self) -> Path:
        """데이터베이스 디렉토리 경로 반환 (db/)"""
        # 프로젝트 루트의 db/ 폴더 사용 (Docker 볼륨 마운트 대응)
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "db"
    
    def _get_master_db_path(self) -> str:
        """마스터 DB 경로 반환 (db/master.db)"""
        return str(self._get_db_dir() / "master.db")
    
    def _get_user_db_path(self, user_id: int) -> str:
        """사용자별 DB 경로 반환 (db/user_{user_id}.db)"""
        return str(self._get_db_dir() / f"user_{user_id}.db")
    
    def _ensure_db_dir(self):
        """데이터베이스 디렉토리 생성"""
        db_dir = self._get_db_dir()
        if not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"데이터베이스 디렉토리 생성: {db_dir}")
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def _create_connection(self, db_path: str) -> sqlite3.Connection:
        """SQLite 연결 생성 (공통 설정 적용)"""
        connection = sqlite3.connect(db_path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        return connection
    
    def get_master_connection(self) -> sqlite3.Connection:
        """마스터 DB 연결 반환 (스레드별 연결)"""
        if not hasattr(self._thread_local, 'master_conn') or self._thread_local.master_conn is None:
            try:
                self._thread_local.master_conn = self._create_connection(self._get_master_db_path())
            except Exception as e:
                logger.error(f"마스터 DB 연결 오류: {e}")
                return None
        return self._thread_local.master_conn
    
    def get_user_connection(self, user_id: int) -> sqlite3.Connection:
        """사용자별 DB 연결 반환 (스레드별 연결)"""
        conn_key = f'user_conn_{user_id}'
        if not hasattr(self._thread_local, conn_key) or getattr(self._thread_local, conn_key) is None:
            try:
                db_path = self._get_user_db_path(user_id)
                conn = self._create_connection(db_path)
                setattr(self._thread_local, conn_key, conn)
                
                # 새 연결 시 사용자 DB 초기화 및 마이그레이션 확인
                self._init_user_db(conn)
                self._migrate_user_db_internal(conn, user_id)
            except Exception as e:
                logger.error(f"사용자 {user_id} DB 연결 오류: {e}")
                return None
        return getattr(self._thread_local, conn_key)
    
    @property
    def conn(self) -> sqlite3.Connection:
        """기존 코드 호환용: 마스터 DB 연결 반환"""
        return self.get_master_connection()
    
    def close_connection(self):
        """현재 스레드의 모든 연결 종료"""
        # 마스터 연결 종료
        if hasattr(self._thread_local, 'master_conn') and self._thread_local.master_conn:
            self._thread_local.master_conn.close()
            self._thread_local.master_conn = None
        
        # 사용자 연결들 종료
        for attr in dir(self._thread_local):
            if attr.startswith('user_conn_'):
                conn = getattr(self._thread_local, attr)
                if conn:
                    conn.close()
                    setattr(self._thread_local, attr, None)
    
    def close_user_connection(self, user_id: int):
        """특정 사용자의 DB 연결 종료"""
        conn_key = f'user_conn_{user_id}'
        if hasattr(self._thread_local, conn_key):
            conn = getattr(self._thread_local, conn_key)
            if conn:
                conn.close()
                setattr(self._thread_local, conn_key, None)
    
    def __del__(self):
        """소멸자에서 연결 정리"""
        self.close_connection()
    
    # =========================================================================
    # Database Initialization
    # =========================================================================
    
    def _init_master_db(self):
        """마스터 데이터베이스 초기화 (사용자 테이블만)"""
        with sqlite3.connect(self._get_master_db_path()) as conn:
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.commit()
            logger.info("마스터 데이터베이스 초기화 완료")
    
    def _init_user_db(self, conn: sqlite3.Connection):
        """사용자별 데이터베이스 테이블 초기화"""
        # ============================================================
        # 1. User Profile & Interests
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                score REAL DEFAULT 0.5,
                source TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_detected_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_survey_responses (
                id INTEGER PRIMARY KEY,
                response_json TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 2. Data Logs
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS browser_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                visit_time DATETIME NOT NULL
            )
        """)
        
        # ============================================================
        # 3. Files
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                doc_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 4. Content Keywords
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS content_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                keyword TEXT NOT NULL,
                original_text TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 5. Recommendations
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendation_blacklist (
                keyword TEXT PRIMARY KEY,
                blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_type TEXT,
                keyword TEXT,
                related_keywords TEXT,
                bubble_message TEXT,
                report_content TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                responded_at DATETIME,
                generated_report_file_id TEXT,
                report_file_path TEXT
            )
        """)
        
        # ============================================================
        # 6. Chat Messages
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 7. User Notes (Dashboard)
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT DEFAULT '',
                content TEXT NOT NULL,
                pinned BOOLEAN DEFAULT 0,
                tags TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 8. Interest History (Dashboard - 관심사 변화 추적)
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                score REAL NOT NULL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # 9. Dashboard Analyses (AI 분석 결과)
        # ============================================================
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                chart_data TEXT,
                insights TEXT,
                query TEXT,
                status TEXT DEFAULT 'completed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ============================================================
        # Indexes
        # ============================================================
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_interests_keyword ON user_interests(keyword)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_logs_visit_time ON browser_logs(visit_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_file_path ON files(file_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_keywords_created_at ON content_keywords(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_keywords_source ON content_keywords(source_type, source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_notes_updated ON user_notes(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_notes_pinned ON user_notes(pinned DESC, updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interest_history_keyword ON interest_history(keyword, recorded_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interest_history_recorded ON interest_history(recorded_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_analyses_type ON dashboard_analyses(analysis_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_analyses_created ON dashboard_analyses(created_at DESC)")
        
        conn.commit()
    
    # =========================================================================
    # Migration Logic
    # =========================================================================
    
    def _get_user_version(self, conn: sqlite3.Connection) -> int:
        """현재 DB의 user_version 조회"""
        cursor = conn.execute("PRAGMA user_version")
        return cursor.fetchone()[0]
    
    def _set_user_version(self, conn: sqlite3.Connection, version: int):
        """DB의 user_version 설정"""
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    
    def _migrate_user_db_internal(self, conn: sqlite3.Connection, user_id: int):
        """사용자 DB 마이그레이션 실행 (내부용)"""
        current_version = self._get_user_version(conn)
        
        if current_version >= CURRENT_DB_VERSION:
            return  # 이미 최신 버전
        
        logger.info(f"사용자 {user_id} DB 마이그레이션 시작: v{current_version} -> v{CURRENT_DB_VERSION}")
        
        try:
            for version in range(current_version + 1, CURRENT_DB_VERSION + 1):
                if version in MIGRATION_SCRIPTS:
                    description, sql_statements = MIGRATION_SCRIPTS[version]
                    logger.info(f"  마이그레이션 v{version}: {description}")
                    
                    for sql in sql_statements:
                        try:
                            conn.execute(sql)
                        except Exception as e:
                            logger.warning(f"  SQL 실행 경고 (무시됨): {e}")
                    
                    conn.commit()
            
            # 최종 버전으로 업데이트
            self._set_user_version(conn, CURRENT_DB_VERSION)
            logger.info(f"사용자 {user_id} DB 마이그레이션 완료: v{CURRENT_DB_VERSION}")
            
        except Exception as e:
            logger.error(f"사용자 {user_id} DB 마이그레이션 오류: {e}")
            conn.rollback()
            raise
    
    def migrate_user_db(self, user_id: int) -> bool:
        """특정 사용자 DB에 대해 마이그레이션 실행 (외부 호출용)
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            성공 여부
        """
        try:
            db_path = self._get_user_db_path(user_id)
            if not os.path.exists(db_path):
                logger.info(f"사용자 {user_id} DB 파일이 없습니다. 마이그레이션 스킵.")
                return True
            
            with sqlite3.connect(db_path) as conn:
                self._init_user_db(conn)  # 테이블 존재 확인
                self._migrate_user_db_internal(conn, user_id)
            
            return True
        except Exception as e:
            logger.error(f"사용자 {user_id} 마이그레이션 실패: {e}")
            return False
    
    def migrate_all_user_dbs(self) -> Dict[str, Any]:
        """모든 사용자 DB 파일에 대해 마이그레이션 실행 (서버 시작 시)
        
        Returns:
            {'success': int, 'failed': int, 'total': int, 'errors': []}
        """
        result = {'success': 0, 'failed': 0, 'total': 0, 'errors': []}
        
        db_dir = self._get_db_dir()
        user_db_pattern = str(db_dir / "user_*.db")
        user_db_files = glob.glob(user_db_pattern)
        
        result['total'] = len(user_db_files)
        logger.info(f"마이그레이션 대상 DB 파일: {result['total']}개")
        
        for db_path in user_db_files:
            try:
                # 파일명에서 user_id 추출: user_123.db -> 123
                filename = os.path.basename(db_path)
                user_id_str = filename.replace('user_', '').replace('.db', '')
                user_id = int(user_id_str)
                
                if self.migrate_user_db(user_id):
                    result['success'] += 1
                else:
                    result['failed'] += 1
                    result['errors'].append(f"user_{user_id}: 마이그레이션 실패")
                    
            except ValueError as e:
                result['failed'] += 1
                result['errors'].append(f"{db_path}: 잘못된 파일명 형식")
            except Exception as e:
                result['failed'] += 1
                result['errors'].append(f"{db_path}: {str(e)}")
        
        logger.info(f"마이그레이션 완료: 성공 {result['success']}, 실패 {result['failed']}")
        return result
    
    def get_all_user_ids_from_files(self) -> List[int]:
        """db/ 폴더에서 모든 user_*.db 파일의 user_id 목록 반환"""
        db_dir = self._get_db_dir()
        user_db_pattern = str(db_dir / "user_*.db")
        user_db_files = glob.glob(user_db_pattern)
        
        user_ids = []
        for db_path in user_db_files:
            try:
                filename = os.path.basename(db_path)
                user_id_str = filename.replace('user_', '').replace('.db', '')
                user_ids.append(int(user_id_str))
            except ValueError:
                continue
        
        return user_ids

    # =========================================================================
    # User Management Methods (Master DB)
    # =========================================================================
    
    def get_or_create_user_by_google(self, google_id: str, email: str, refresh_token: str = None) -> Optional[Dict[str, Any]]:
        """Google ID로 사용자를 조회하고, 없으면 새로 생성 후 user 객체를 반환"""
        try:
            conn = self.get_master_connection()
            conn.row_factory = sqlite3.Row
            
            # 기존 사용자 조회
            cursor = conn.execute(
                "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
            )
            row = cursor.fetchone()
            
            if row:
                # 사용자 존재 - refresh_token 업데이트 (제공된 경우)
                if refresh_token:
                    conn.execute(
                        "UPDATE users SET refresh_token = ? WHERE google_user_id = ?",
                        (refresh_token, google_id)
                    )
                    conn.commit()
                    cursor = conn.execute(
                        "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
                    )
                    row = cursor.fetchone()
                
                user_dict = dict(row) if row else None
                
                # 사용자 DB 초기화 확인
                if user_dict:
                    self.get_user_connection(user_dict['user_id'])
                
                return user_dict
            else:
                # 새 사용자 생성
                cursor = conn.execute("""
                    INSERT INTO users (google_user_id, email, refresh_token, has_completed_setup)
                    VALUES (?, ?, ?, 0)
                """, (google_id, email, refresh_token))
                conn.commit()
                
                cursor = conn.execute(
                    "SELECT * FROM users WHERE google_user_id = ?", (google_id,)
                )
                row = cursor.fetchone()
                user_dict = dict(row) if row else None
                
                # 새 사용자 DB 초기화
                if user_dict:
                    self.get_user_connection(user_dict['user_id'])
                
                return user_dict
                
        except Exception as e:
            logger.error(f"사용자 조회/생성 오류: {e}")
            conn = self.get_master_connection()
            if conn:
                conn.rollback()
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """user_id로 사용자 정보 조회"""
        try:
            conn = self.get_master_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"사용자 조회 오류: {e}")
            return None
    
    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        """google_user_id로 사용자 정보 조회"""
        try:
            conn = self.get_master_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
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
            conn = self.get_master_connection()
            conn.execute(
                "UPDATE users SET has_completed_setup = ? WHERE user_id = ?",
                (status, user_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"사용자 설정 상태 업데이트 오류: {e}")
            conn = self.get_master_connection()
            if conn:
                conn.rollback()
            return False
    
    def update_user_folder(self, user_id: int, folder_path: str) -> bool:
        """selected_root_folder 경로를 업데이트"""
        try:
            conn = self.get_master_connection()
            conn.execute(
                "UPDATE users SET selected_root_folder = ? WHERE user_id = ?",
                (folder_path, user_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"사용자 폴더 경로 업데이트 오류: {e}")
            conn = self.get_master_connection()
            if conn:
                conn.rollback()
            return False
    
    def get_user_folder(self, user_id: int) -> Optional[str]:
        """user_id로 selected_root_folder 경로를 조회"""
        try:
            conn = self.get_master_connection()
            cursor = conn.execute(
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
            conn = self.get_master_connection()
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT user_id, email FROM users")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"모든 사용자 조회 오류: {e}")
            return []

    # =========================================================================
    # User Interests Methods (User DB)
    # =========================================================================
    
    def upsert_interest(self, user_id: int, keyword: str, score: float = 0.5, source: str = 'manual') -> bool:
        """관심사 업서트"""
        try:
            conn = self.get_user_connection(user_id)
            
            cursor = conn.execute(
                "SELECT id, score FROM user_interests WHERE keyword = ?",
                (keyword,)
            )
            row = cursor.fetchone()
            
            if row:
                new_score = min(1.0, (row[1] + score) / 2 + 0.1)
                conn.execute("""
                    UPDATE user_interests 
                    SET score = ?, last_detected_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_score, row[0]))
            else:
                conn.execute("""
                    INSERT INTO user_interests (keyword, score, source)
                    VALUES (?, ?, ?)
                """, (keyword, score, source))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"관심사 업서트 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def get_user_interests(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """사용자 관심사 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM user_interests 
                ORDER BY score DESC, last_detected_at DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"관심사 조회 오류: {e}")
            return []
    
    def delete_interest(self, user_id: int, keyword: str) -> bool:
        """관심사 삭제"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("DELETE FROM user_interests WHERE keyword = ?", (keyword,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"관심사 삭제 오류: {e}")
            return False

    # =========================================================================
    # Survey Response Methods (User DB)
    # =========================================================================
    
    def upsert_survey_response(self, user_id: int, response_data: Dict[str, Any]) -> bool:
        """설문지 응답 저장 (업서트)"""
        try:
            conn = self.get_user_connection(user_id)
            response_json = json.dumps(response_data, ensure_ascii=False)
            conn.execute("""
                INSERT INTO user_survey_responses (id, response_json, updated_at)
                VALUES (1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (response_json,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"설문지 응답 저장 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def get_survey_response(self, user_id: int) -> Optional[Dict[str, Any]]:
        """사용자 설문지 응답 조회"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT response_json FROM user_survey_responses WHERE id = 1")
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
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT 1 FROM user_survey_responses WHERE id = 1 LIMIT 1")
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"설문지 완료 여부 확인 오류: {e}")
            return False

    # =========================================================================
    # Browser Logs Methods (User DB)
    # =========================================================================
    
    def insert_browser_log(self, user_id: int, url: str, title: str = None, 
                           visit_time: datetime = None) -> Optional[int]:
        """브라우저 로그 삽입"""
        try:
            conn = self.get_user_connection(user_id)
            if visit_time is None:
                visit_time = datetime.now()
            
            cursor = conn.execute("""
                INSERT INTO browser_logs (url, title, visit_time)
                VALUES (?, ?, ?)
            """, (url, title, visit_time))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"브라우저 로그 삽입 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return None
    
    def get_browser_logs(self, user_id: int, limit: int = 100, 
                         since: datetime = None) -> List[Dict[str, Any]]:
        """브라우저 로그 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            if since:
                cursor = conn.execute("""
                    SELECT * FROM browser_logs 
                    WHERE visit_time >= ?
                    ORDER BY visit_time DESC LIMIT ?
                """, (since, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM browser_logs 
                    ORDER BY visit_time DESC LIMIT ?
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"브라우저 로그 조회 오류: {e}")
            return []
    
    def is_browser_log_duplicate(self, user_id: int, url: str, visit_time: datetime) -> bool:
        """브라우저 로그 중복 확인"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("""
                SELECT 1 FROM browser_logs 
                WHERE url = ? AND visit_time = ? 
                LIMIT 1
            """, (url, visit_time))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"브라우저 로그 중복 체크 오류: {e}")
            return False

    # =========================================================================
    # Files Methods (User DB)
    # =========================================================================
    
    def upsert_file(self, doc_id: str, user_id: int, file_path: str) -> bool:
        """파일 정보 업서트"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("""
                INSERT INTO files (doc_id, file_path, processed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(doc_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    processed_at = CURRENT_TIMESTAMP
            """, (doc_id, file_path))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"파일 업서트 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def get_file(self, user_id: int, doc_id: str) -> Optional[Dict[str, Any]]:
        """파일 정보 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM files WHERE doc_id = ?", (doc_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"파일 조회 오류: {e}")
            return None
    
    def find_file_by_path(self, user_id: int, file_path: str) -> Optional[str]:
        """경로로 파일 doc_id 찾기"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT doc_id FROM files WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"파일 경로 조회 오류: {e}")
            return None
    
    def is_file_exists(self, user_id: int, doc_id: str) -> bool:
        """파일이 이미 존재하는지 확인"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT 1 FROM files WHERE doc_id = ? LIMIT 1", (doc_id,))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"파일 존재 확인 오류: {e}")
            return False
    
    def get_file_last_modified(self, user_id: int, file_path: str) -> Optional[datetime]:
        """파일의 마지막 처리 시간 조회"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute(
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
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM files 
                ORDER BY processed_at DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"파일 조회 오류: {e}")
            return []

    # =========================================================================
    # Content Keywords Methods (User DB)
    # =========================================================================
    
    def insert_content_keyword(self, user_id: int, source_type: str, source_id: str,
                                keyword: str, original_text: str = None) -> Optional[int]:
        """콘텐츠 키워드 삽입"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("""
                INSERT INTO content_keywords (source_type, source_id, keyword, original_text)
                VALUES (?, ?, ?, ?)
            """, (source_type, source_id, keyword, original_text))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"콘텐츠 키워드 삽입 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return None
    
    def insert_content_keywords_batch(self, user_id: int, keywords: List[Dict[str, Any]]) -> int:
        """콘텐츠 키워드 일괄 삽입"""
        if not keywords:
            return 0
        
        try:
            conn = self.get_user_connection(user_id)
            inserted = 0
            conn.execute("BEGIN TRANSACTION")
            for kw in keywords:
                conn.execute("""
                    INSERT INTO content_keywords (source_type, source_id, keyword, original_text)
                    VALUES (?, ?, ?, ?)
                """, (
                    kw['source_type'],
                    kw['source_id'],
                    kw['keyword'],
                    kw.get('original_text')
                ))
                inserted += 1
            conn.commit()
            return inserted
        except Exception as e:
            logger.error(f"콘텐츠 키워드 일괄 삽입 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return 0
    
    def get_content_keywords(self, user_id: int, source_type: str = None,
                              limit: int = 100, since: datetime = None) -> List[Dict[str, Any]]:
        """콘텐츠 키워드 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM content_keywords WHERE 1=1"
            params = []
            
            if source_type:
                query += " AND source_type = ?"
                params.append(source_type)
            
            if since:
                query += " AND created_at >= ?"
                params.append(since)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"콘텐츠 키워드 조회 오류: {e}")
            return []
    
    def get_keywords_by_source(self, user_id: int, source_type: str, source_id: str) -> List[Dict[str, Any]]:
        """특정 소스의 키워드 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
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
        """키워드 빈도 분석"""
        try:
            conn = self.get_user_connection(user_id)
            query = """
                SELECT keyword, COUNT(*) as count, COUNT(DISTINCT source_id) as sources
                FROM content_keywords 
                WHERE 1=1
            """
            params = []
            
            if since:
                query += " AND created_at >= ?"
                params.append(since)
            
            query += " GROUP BY keyword ORDER BY count DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [{'keyword': row[0], 'count': row[1], 'sources': row[2]} 
                    for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"키워드 빈도 분석 오류: {e}")
            return []
    
    def delete_keywords_by_source(self, user_id: int, source_type: str, source_id: str) -> bool:
        """특정 소스의 키워드 삭제"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute(
                "DELETE FROM content_keywords WHERE source_type = ? AND source_id = ?",
                (source_type, source_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"키워드 삭제 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False

    # =========================================================================
    # Recommendation Blacklist Methods (User DB)
    # =========================================================================
    
    def add_to_blacklist(self, user_id: int, keyword: str) -> bool:
        """키워드를 블랙리스트에 추가"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("""
                INSERT OR IGNORE INTO recommendation_blacklist (keyword)
                VALUES (?)
            """, (keyword,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"블랙리스트 추가 오류: {e}")
            return False
    
    def remove_from_blacklist(self, user_id: int, keyword: str) -> bool:
        """키워드를 블랙리스트에서 제거"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("DELETE FROM recommendation_blacklist WHERE keyword = ?", (keyword,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"블랙리스트 제거 오류: {e}")
            return False
    
    def get_blacklist(self, user_id: int) -> List[str]:
        """사용자의 블랙리스트 조회"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT keyword FROM recommendation_blacklist")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"블랙리스트 조회 오류: {e}")
            return []
    
    def is_keyword_blacklisted(self, user_id: int, keyword: str) -> bool:
        """키워드가 블랙리스트에 있는지 확인"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute(
                "SELECT 1 FROM recommendation_blacklist WHERE keyword = ? LIMIT 1",
                (keyword,)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"블랙리스트 확인 오류: {e}")
            return False

    # =========================================================================
    # Recommendations Methods (User DB)
    # =========================================================================
    
    def create_recommendation(self, user_id: int, trigger_type: str, keyword: str,
                              bubble_message: str, related_keywords: List[str] = None,
                              report_content: str = None) -> int:
        """새로운 추천 생성"""
        try:
            conn = self.get_user_connection(user_id)
            related_json = json.dumps(related_keywords or [], ensure_ascii=False)
            cursor = conn.execute("""
                INSERT INTO recommendations 
                (trigger_type, keyword, related_keywords, bubble_message, report_content, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (trigger_type, keyword, related_json, bubble_message, report_content))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"추천 생성 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return -1
    
    def get_recommendation(self, user_id: int, recommendation_id: int) -> Optional[Dict[str, Any]]:
        """추천 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,))
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
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM recommendations 
                WHERE status = 'pending'
                ORDER BY created_at DESC
            """)
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
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM recommendations 
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
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
    
    def update_recommendation_status(self, user_id: int, recommendation_id: int, status: str) -> bool:
        """추천 상태 업데이트"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("""
                UPDATE recommendations 
                SET status = ?, responded_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (status, recommendation_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"추천 상태 업데이트 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def update_recommendation_report(self, user_id: int, recommendation_id: int, 
                                      report_content: str, report_file_id: str = None) -> bool:
        """추천 보고서 업데이트"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("""
                UPDATE recommendations 
                SET report_content = ?, generated_report_file_id = ?
                WHERE id = ?
            """, (report_content, report_file_id, recommendation_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"추천 보고서 업데이트 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def update_recommendation_report_path(self, user_id: int, recommendation_id: int, 
                                          report_file_path: str) -> bool:
        """추천의 보고서 파일 경로 업데이트"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("""
                UPDATE recommendations 
                SET report_file_path = ?
                WHERE id = ?
            """, (report_file_path, recommendation_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"추천 보고서 경로 업데이트 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False
    
    def get_recent_recommendation_count(self, user_id: int, trigger_type: str = None, 
                                         hours: int = 1) -> int:
        """지정된 시간 내에 생성된 추천 개수 조회"""
        try:
            conn = self.get_user_connection(user_id)
            since = datetime.now() - timedelta(hours=hours)
            if trigger_type:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM recommendations
                    WHERE trigger_type = ? AND created_at >= ?
                """, (trigger_type, since))
            else:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM recommendations
                    WHERE created_at >= ?
                """, (since,))
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"최근 추천 횟수 조회 오류: {e}")
            return 0

    # =========================================================================
    # Chat Messages Methods (User DB)
    # =========================================================================
    
    def log_chat_message(self, user_id: int, role: str, content: str, 
                         metadata: Optional[Dict[str, Any]] = None) -> int:
        """채팅 메시지 저장 (최대 4000자로 truncate)"""
        try:
            conn = self.get_user_connection(user_id)
            truncated_content = content[:4000] if content else ""
            metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
            
            cursor = conn.execute("""
                INSERT INTO chat_messages (role, content, metadata)
                VALUES (?, ?, ?)
            """, (role, truncated_content, metadata_json))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"채팅 메시지 저장 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return -1
    
    def get_recent_chat_messages(self, user_id: int, limit: int = 12) -> List[Dict[str, Any]]:
        """최근 채팅 메시지 조회 (oldest→newest 순서로 반환)"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM (
                    SELECT * FROM chat_messages 
                    ORDER BY created_at DESC 
                    LIMIT ?
                ) ORDER BY created_at ASC
            """, (limit,))
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                msg = dict(row)
                if msg.get('metadata'):
                    try:
                        msg['metadata'] = json.loads(msg['metadata'])
                    except json.JSONDecodeError:
                        pass
                result.append(msg)
            return result
        except Exception as e:
            logger.error(f"채팅 메시지 조회 오류: {e}")
            return []
    
    def delete_chat_messages_for_user(self, user_id: int) -> bool:
        """사용자의 모든 채팅 메시지 삭제"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("DELETE FROM chat_messages")
            conn.commit()
            logger.info(f"사용자 {user_id}의 채팅 메시지가 삭제되었습니다.")
            return True
        except Exception as e:
            logger.error(f"채팅 메시지 삭제 오류: {e}")
            conn = self.get_user_connection(user_id)
            if conn:
                conn.rollback()
            return False

    # =========================================================================
    # Statistics Methods
    # =========================================================================
    
    def get_collection_stats(self, user_id: int = None) -> Dict[str, int]:
        """데이터 수집 통계 조회"""
        try:
            stats = {}
            if user_id:
                conn = self.get_user_connection(user_id)
                cursor = conn.execute("SELECT COUNT(*) FROM files")
                stats['files'] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM browser_logs")
                stats['browser_logs'] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM content_keywords")
                stats['content_keywords'] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM chat_messages")
                stats['chat_messages'] = cursor.fetchone()[0]
            else:
                # 전체 통계: 모든 사용자 DB 합산
                stats = {'files': 0, 'browser_logs': 0, 'content_keywords': 0, 'chat_messages': 0}
                for uid in self.get_all_user_ids_from_files():
                    user_stats = self.get_collection_stats(uid)
                    for key in stats:
                        stats[key] += user_stats.get(key, 0)
            return stats
        except Exception as e:
            logger.error(f"수집 통계 조회 오류: {e}")
            return {}

    # =========================================================================
    # Data Cleanup Methods
    # =========================================================================
    
    def delete_user_data(self, user_id: int) -> bool:
        """사용자 관련 모든 데이터 삭제 (DB 파일 삭제)"""
        try:
            # 사용자 DB 연결 종료
            self.close_user_connection(user_id)
            
            # 사용자 DB 파일 삭제
            db_path = self._get_user_db_path(user_id)
            if os.path.exists(db_path):
                os.remove(db_path)
                logger.info(f"사용자 {user_id} DB 파일 삭제됨: {db_path}")
            
            # WAL 파일도 삭제
            wal_path = db_path + "-wal"
            shm_path = db_path + "-shm"
            for path in [wal_path, shm_path]:
                if os.path.exists(path):
                    os.remove(path)
            
            # 마스터 DB에서 사용자 삭제
            conn = self.get_master_connection()
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()
            
            logger.info(f"사용자 {user_id}의 모든 데이터가 삭제되었습니다.")
            return True
        except Exception as e:
            logger.error(f"사용자 데이터 삭제 오류: {e}")
            return False

    # =========================================================================
    # Legacy Compatibility Methods
    # =========================================================================
    
    def get_last_browser_collection_time(self, user_id: int, browser_name: str = None) -> Optional[datetime]:
        """마지막 브라우저 히스토리 수집 시간 조회"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("SELECT MAX(visit_time) FROM browser_logs")
            row = cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0]) if isinstance(row[0], str) else row[0]
            return None
        except Exception as e:
            logger.error(f"마지막 브라우저 수집 시간 조회 오류: {e}")
            return None
    
    def insert_collected_file(self, file_info: Dict[str, Any]) -> bool:
        """수집된 파일 정보 저장 (Legacy 호환)"""
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
        """수집된 브라우저 히스토리 저장 (Legacy 호환)"""
        return self.insert_browser_log(
            user_id=history_info['user_id'],
            url=history_info.get('url', ''),
            title=history_info.get('title'),
            visit_time=history_info.get('visit_time')
        )
    
    def get_user_survey_response(self, user_id: int) -> Optional[Dict[str, Any]]:
        """사용자 설문지 응답 조회 (Legacy 호환)"""
        return self.get_survey_response(user_id)
    
    def insert_survey_response(self, user_id: int, survey_data: Dict[str, Any]) -> bool:
        """설문지 응답 저장 (Legacy 호환)"""
        return self.upsert_survey_response(user_id, survey_data)
    
    def get_unread_recommendations(self, user_id: int) -> List[Dict[str, Any]]:
        """읽지 않은(pending) 추천 목록 조회 (Legacy 호환)"""
        return self.get_pending_recommendations(user_id)
    
    def mark_recommendation_as_read(self, user_id: int, recommendation_id: int) -> bool:
        """추천을 읽음으로 표시 (Legacy 호환)"""
        return self.update_recommendation_status(user_id, recommendation_id, 'shown')
    
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
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM files
                WHERE processed_at >= ?
                ORDER BY processed_at DESC
            """, (since,))
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

    # =========================================================================
    # Dashboard: User Notes Methods
    # =========================================================================
    
    def create_note(self, user_id: int, content: str, title: str = "", 
                    pinned: bool = False, tags: str = None) -> Optional[int]:
        """새 노트 생성"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("""
                INSERT INTO user_notes (title, content, pinned, tags)
                VALUES (?, ?, ?, ?)
            """, (title, content, 1 if pinned else 0, tags))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"노트 생성 오류: {e}")
            return None
    
    def update_note(self, user_id: int, note_id: int, content: str = None, 
                    title: str = None, pinned: bool = None, tags: str = None) -> bool:
        """노트 업데이트"""
        try:
            conn = self.get_user_connection(user_id)
            updates = []
            params = []
            
            if content is not None:
                updates.append("content = ?")
                params.append(content)
            if title is not None:
                updates.append("title = ?")
                params.append(title)
            if pinned is not None:
                updates.append("pinned = ?")
                params.append(1 if pinned else 0)
            if tags is not None:
                updates.append("tags = ?")
                params.append(tags)
            
            if not updates:
                return True
            
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(note_id)
            
            conn.execute(f"""
                UPDATE user_notes SET {', '.join(updates)} WHERE id = ?
            """, params)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"노트 업데이트 오류: {e}")
            return False
    
    def delete_note(self, user_id: int, note_id: int) -> bool:
        """노트 삭제"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("DELETE FROM user_notes WHERE id = ?", (note_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"노트 삭제 오류: {e}")
            return False
    
    def get_notes(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """노트 목록 조회 (고정된 노트 우선, 최신순)"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM user_notes 
                ORDER BY pinned DESC, updated_at DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"노트 조회 오류: {e}")
            return []
    
    def get_note_by_id(self, user_id: int, note_id: int) -> Optional[Dict[str, Any]]:
        """특정 노트 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM user_notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"노트 조회 오류: {e}")
            return None

    # =========================================================================
    # Dashboard: Interest History & Trend Methods
    # =========================================================================
    
    def record_interest_snapshot(self, user_id: int) -> bool:
        """현재 관심사 스냅샷을 히스토리에 기록"""
        try:
            conn = self.get_user_connection(user_id)
            interests = self.get_user_interests(user_id, limit=100)
            
            for interest in interests:
                conn.execute("""
                    INSERT INTO interest_history (keyword, score, recorded_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (interest['keyword'], interest['score']))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"관심사 스냅샷 기록 오류: {e}")
            return False
    
    def get_interest_trend(self, user_id: int, days: int = 30, 
                           keyword: str = None) -> List[Dict[str, Any]]:
        """관심사 트렌드 조회 (날짜별 스코어 변화)"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            
            if keyword:
                cursor = conn.execute("""
                    SELECT keyword, score, recorded_at,
                           DATE(recorded_at) as date
                    FROM interest_history
                    WHERE keyword = ? 
                      AND recorded_at >= datetime('now', ?)
                    ORDER BY recorded_at ASC
                """, (keyword, f'-{days} days'))
            else:
                cursor = conn.execute("""
                    SELECT keyword, score, recorded_at,
                           DATE(recorded_at) as date
                    FROM interest_history
                    WHERE recorded_at >= datetime('now', ?)
                    ORDER BY recorded_at ASC
                """, (f'-{days} days',))
            
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"관심사 트렌드 조회 오류: {e}")
            return []
    
    def get_interest_summary(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """관심사 요약 통계 (대시보드용)"""
        try:
            conn = self.get_user_connection(user_id)
            
            # 현재 관심사 개수
            cursor = conn.execute("SELECT COUNT(*) FROM user_interests")
            total_interests = cursor.fetchone()[0]
            
            # 상위 관심사
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT keyword, score FROM user_interests 
                ORDER BY score DESC LIMIT 5
            """)
            top_interests = [dict(row) for row in cursor.fetchall()]
            
            # 최근 추가된 관심사 (7일 이내)
            cursor = conn.execute("""
                SELECT keyword, score, created_at FROM user_interests 
                WHERE created_at >= datetime('now', '-7 days')
                ORDER BY created_at DESC LIMIT 5
            """)
            recent_interests = [dict(row) for row in cursor.fetchall()]
            
            # 최근 활성화된 관심사 (최근 업데이트)
            cursor = conn.execute("""
                SELECT keyword, score, last_detected_at FROM user_interests 
                ORDER BY last_detected_at DESC LIMIT 5
            """)
            active_interests = [dict(row) for row in cursor.fetchall()]
            
            return {
                "total_count": total_interests,
                "top_interests": top_interests,
                "recent_interests": recent_interests,
                "active_interests": active_interests
            }
        except Exception as e:
            logger.error(f"관심사 요약 조회 오류: {e}")
            return {"total_count": 0, "top_interests": [], "recent_interests": [], "active_interests": []}

    # =========================================================================
    # Dashboard: Activity Summary
    # =========================================================================
    
    def get_activity_summary(self, user_id: int, days: int = 7) -> Dict[str, Any]:
        """사용자 활동 요약 (대시보드용)"""
        try:
            conn = self.get_user_connection(user_id)
            
            # 기준 시간 계산 (Python datetime으로 계산하여 SQLite 비교와 호환)
            from datetime import datetime, timedelta
            cutoff_time = datetime.now() - timedelta(days=days)
            cutoff_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')
            
            # 채팅 메시지 수
            cursor = conn.execute("""
                SELECT COUNT(*) FROM chat_messages 
                WHERE created_at >= ?
            """, (cutoff_str,))
            chat_count = cursor.fetchone()[0]
            
            # 브라우저 로그 수
            cursor = conn.execute("""
                SELECT COUNT(*) FROM browser_logs 
                WHERE visit_time >= ?
            """, (cutoff_str,))
            browser_count = cursor.fetchone()[0]
            
            # 추천 수
            cursor = conn.execute("""
                SELECT COUNT(*), 
                       SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) as accepted,
                       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                FROM recommendations 
                WHERE created_at >= ?
            """, (cutoff_str,))
            rec_row = cursor.fetchone()
            rec_total = rec_row[0] or 0
            rec_accepted = rec_row[1] or 0
            rec_rejected = rec_row[2] or 0
            
            # 파일 수
            cursor = conn.execute("""
                SELECT COUNT(*) FROM files 
                WHERE processed_at >= ?
            """, (cutoff_str,))
            file_count = cursor.fetchone()[0]
            
            # 마지막 활동 시간
            cursor = conn.execute("""
                SELECT MAX(created_at) FROM chat_messages
            """)
            last_chat = cursor.fetchone()[0]
            
            return {
                "period_days": days,
                "chat_messages": chat_count,
                "browser_visits": browser_count,
                "files_processed": file_count,
                "recommendations": {
                    "total": rec_total,
                    "accepted": rec_accepted,
                    "rejected": rec_rejected
                },
                "last_chat_at": last_chat
            }
        except Exception as e:
            logger.error(f"활동 요약 조회 오류: {e}")
            return {
                "period_days": days,
                "chat_messages": 0,
                "browser_visits": 0,
                "files_processed": 0,
                "recommendations": {"total": 0, "accepted": 0, "rejected": 0},
                "last_chat_at": None
            }


    # =========================================================================
    # Dashboard: AI Analysis Results
    # =========================================================================
    
    def create_analysis(self, user_id: int, analysis_type: str, title: str, 
                        content: str, chart_data: Dict = None, 
                        insights: List[str] = None, query: str = None) -> Optional[int]:
        """AI 분석 결과 저장"""
        try:
            conn = self.get_user_connection(user_id)
            chart_json = json.dumps(chart_data, ensure_ascii=False) if chart_data else None
            insights_json = json.dumps(insights, ensure_ascii=False) if insights else None
            
            cursor = conn.execute("""
                INSERT INTO dashboard_analyses 
                (analysis_type, title, content, chart_data, insights, query)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (analysis_type, title, content, chart_json, insights_json, query))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"분석 결과 저장 오류: {e}")
            return None
    
    def get_latest_analysis(self, user_id: int) -> Optional[Dict[str, Any]]:
        """최신 분석 결과 1개 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM dashboard_analyses 
                WHERE status = 'completed'
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get('chart_data'):
                    result['chart_data'] = json.loads(result['chart_data'])
                if result.get('insights'):
                    result['insights'] = json.loads(result['insights'])
                return result
            return None
        except Exception as e:
            logger.error(f"최신 분석 조회 오류: {e}")
            return None
    
    def get_all_analyses(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """모든 분석 결과 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM dashboard_analyses 
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get('chart_data'):
                    item['chart_data'] = json.loads(item['chart_data'])
                if item.get('insights'):
                    item['insights'] = json.loads(item['insights'])
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"분석 결과 조회 오류: {e}")
            return []
    
    def get_analysis_by_id(self, user_id: int, analysis_id: int) -> Optional[Dict[str, Any]]:
        """특정 분석 결과 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM dashboard_analyses WHERE id = ?", (analysis_id,)
            )
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get('chart_data'):
                    result['chart_data'] = json.loads(result['chart_data'])
                if result.get('insights'):
                    result['insights'] = json.loads(result['insights'])
                return result
            return None
        except Exception as e:
            logger.error(f"분석 결과 조회 오류: {e}")
            return None
    
    def delete_analysis(self, user_id: int, analysis_id: int) -> bool:
        """분석 결과 삭제"""
        try:
            conn = self.get_user_connection(user_id)
            conn.execute("DELETE FROM dashboard_analyses WHERE id = ?", (analysis_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"분석 결과 삭제 오류: {e}")
            return False
    
    def get_analyses_by_type(self, user_id: int, analysis_type: str, 
                             limit: int = 10) -> List[Dict[str, Any]]:
        """유형별 분석 결과 조회"""
        try:
            conn = self.get_user_connection(user_id)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM dashboard_analyses 
                WHERE analysis_type = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (analysis_type, limit))
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get('chart_data'):
                    item['chart_data'] = json.loads(item['chart_data'])
                if item.get('insights'):
                    item['insights'] = json.loads(item['insights'])
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"유형별 분석 조회 오류: {e}")
            return []

    def is_file_already_collected(self, user_id: int, file_path: str, file_hash: str) -> bool:
        """파일이 이미 수집되었는지 확인 (경로 또는 해시로 확인)"""
        try:
            conn = self.get_user_connection(user_id)
            cursor = conn.execute("""
                SELECT 1 FROM files 
                WHERE file_path = ? OR doc_id = ?
                LIMIT 1
            """, (file_path, f"file_{file_hash}"))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"파일 중복 확인 오류: {e}")
            return False


# Backward compatibility alias
SQLiteMeta = SQLite
