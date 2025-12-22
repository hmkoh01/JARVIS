"""
JARVIS Client-Side Data Collector
로컬에서 파일과 브라우저 히스토리를 수집하고 백엔드로 업로드합니다.

백엔드에서 DocumentParser(Docling)를 사용하여 파싱합니다.
"""

import os
import hashlib
import logging
import sqlite3
import shutil
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional
from threading import Event
from datetime import datetime, timedelta

import requests
from PyQt6.QtCore import pyqtSignal, QThread

try:
    from config import API_BASE_URL
    print(f"✅ data_collector: config import 성공 - API_BASE_URL={API_BASE_URL}")
except ImportError as e:
    print(f"⚠️ data_collector: config import 실패: {e}")
    API_BASE_URL = "http://localhost:8000"

logger = logging.getLogger(__name__)

# 디버그: 모듈 로드 확인
print(f"📦 data_collector.py 모듈 로드됨 - API: {API_BASE_URL}")


class ClientDataCollector(QThread):
    """
    클라이언트 측 데이터 수집 워커.
    
    로컬에서 파일과 브라우저 히스토리를 수집하고 백엔드로 업로드합니다.
    백엔드에서 DocumentParser(Docling)를 사용하여 파싱합니다.
    
    Signals:
        progress_updated: (progress: float, message: str) 진행 상태 업데이트
        file_processed: (file_name: str) 파일 처리 완료
        collection_completed: 수집 완료
        collection_error: (error_msg: str) 오류 발생
    """
    
    progress_updated = pyqtSignal(float, str)
    file_processed = pyqtSignal(str)
    collection_completed = pyqtSignal()
    collection_error = pyqtSignal(str)
    
    # 지원하는 파일 확장자 (백엔드 DocumentParser와 동일)
    SUPPORTED_EXTENSIONS = {
        'document': ['.txt', '.md', '.rtf', '.pdf', '.docx', '.doc', '.odt', '.rst'],
        'spreadsheet': ['.xlsx', '.xls', '.csv', '.tsv', '.ods'],
        'presentation': ['.pptx', '.ppt', '.odp'],
        'code': ['.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss', 
                 '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs', 
                 '.swift', '.kt', '.r', '.sh', '.bat', '.ps1', '.sql', '.json', 
                 '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf'],
    }
    
    # 스킵할 디렉토리 패턴
    SKIP_PATTERNS = [
        'Windows', 'Program Files', 'Program Files (x86)', '$Recycle.Bin', 
        '.git', 'node_modules', '__pycache__', 'AppData', '.venv', 'venv',
        'site-packages', '.idea', '.vscode', 'build', 'dist', '.cache',
        'Library', 'Applications', 'System'
    ]
    
    # 최대 파일 크기 (50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024
    
    def __init__(
        self,
        user_id: int,
        token: str,
        selected_folders: List[str],
        parent=None
    ):
        super().__init__(parent)
        
        self.user_id = user_id
        self.token = token
        self.selected_folders = selected_folders
        self._stop_event = Event()
        
        # 지원하는 확장자 집합
        self.allowed_extensions = set()
        for exts in self.SUPPORTED_EXTENSIONS.values():
            self.allowed_extensions.update(exts)
    
    def stop(self):
        """수집 중지"""
        self._stop_event.set()
    
    def run(self):
        """데이터 수집 실행 (파일 + 브라우저 히스토리)"""
        print(f"🔄 ClientDataCollector.run() 시작 - folders: {self.selected_folders}")
        print(f"   user_id: {self.user_id}, token: {self.token[:20]}...")
        
        try:
            # ========== 1단계: 파일 수집 (0% ~ 60%) ==========
            self.progress_updated.emit(0.0, "📁 파일 스캔 시작...")
            
            print("📁 파일 스캔 중...")
            files_to_process = self._scan_files()
            print(f"📁 스캔 완료: {len(files_to_process)}개 파일 발견")
            
            if self._stop_event.is_set():
                print("⏹️ 수집 중지됨")
                return
            
            total_files = len(files_to_process)
            file_success_count = 0
            file_skipped_count = 0
            
            if total_files > 0:
                self.progress_updated.emit(5.0, f"📄 {total_files}개 파일 발견")
                
                for i, file_path in enumerate(files_to_process):
                    if self._stop_event.is_set():
                        return
                    
                    result = self._upload_file(file_path)
                    
                    if result:
                        if result.get('skipped'):
                            file_skipped_count += 1
                        else:
                            file_success_count += 1
                            self.file_processed.emit(Path(file_path).name)
                    
                    progress = 5.0 + ((i + 1) / total_files) * 55.0
                    self.progress_updated.emit(
                        progress, 
                        f"📤 파일 업로드 중... ({i + 1}/{total_files})"
                    )
            else:
                self.progress_updated.emit(60.0, "⚠️ 수집할 파일이 없습니다.")
            
            print(f"📁 파일 수집 완료: {file_success_count}개 처리, {file_skipped_count}개 스킵")
            
            # ========== 2단계: 브라우저 히스토리 수집 (60% ~ 90%) ==========
            if self._stop_event.is_set():
                return
            
            self.progress_updated.emit(60.0, "🌐 브라우저 히스토리 수집 중...")
            print("🌐 브라우저 히스토리 수집 시작...")
            
            browser_history = self._collect_browser_history()
            history_count = len(browser_history)
            print(f"🌐 브라우저 히스토리 수집 완료: {history_count}개 항목")
            
            if history_count > 0:
                self.progress_updated.emit(70.0, f"🌐 {history_count}개 히스토리 업로드 중...")
                
                # 브라우저 히스토리 업로드
                upload_success = self._upload_browser_history(browser_history)
                
                if upload_success:
                    self.progress_updated.emit(90.0, f"✅ 브라우저 히스토리 {history_count}개 업로드 완료")
                else:
                    self.progress_updated.emit(90.0, "⚠️ 브라우저 히스토리 업로드 실패")
            else:
                self.progress_updated.emit(90.0, "ℹ️ 수집할 브라우저 히스토리가 없습니다.")
            
            # ========== 3단계: 완료 알림 (90% ~ 100%) ==========
            if self._stop_event.is_set():
                return
            
            self.progress_updated.emit(95.0, "📤 서버에 완료 알림...")
            self._notify_completion()
            
            message = f"✅ 완료! 파일 {file_success_count}개, 히스토리 {history_count}개"
            self.progress_updated.emit(100.0, message)
            print(message)
            self.collection_completed.emit()
            
        except Exception as e:
            logger.error(f"데이터 수집 오류: {e}", exc_info=True)
            self.collection_error.emit(f"수집 오류: {str(e)}")
    
    # =========================================================================
    # 파일 수집 관련 메서드
    # =========================================================================
    
    def _scan_files(self) -> List[str]:
        """선택된 폴더에서 파일 목록 스캔"""
        files = []
        
        print(f"🔍 _scan_files: 스캔 대상 폴더 {len(self.selected_folders)}개")
        
        for folder in self.selected_folders:
            print(f"   📂 폴더 검사: {folder}")
            
            if self._stop_event.is_set():
                break
            
            folder_path = Path(folder)
            if not folder_path.exists():
                print(f"   ⚠️ 폴더 존재하지 않음: {folder}")
                continue
            if not folder_path.is_dir():
                print(f"   ⚠️ 디렉토리가 아님: {folder}")
                continue
            
            print(f"   ✅ 폴더 유효함: {folder}")
            
            try:
                for root, dirs, filenames in os.walk(folder_path):
                    if self._stop_event.is_set():
                        break
                    
                    # 스킵할 디렉토리 필터링
                    dirs[:] = [
                        d for d in dirs 
                        if not any(skip in d for skip in self.SKIP_PATTERNS)
                        and not d.startswith('.')
                    ]
                    
                    for filename in filenames:
                        if self._stop_event.is_set():
                            break
                        
                        # 임시 파일 스킵
                        if filename.startswith('~$') or filename.startswith('.'):
                            continue
                        
                        file_path = os.path.join(root, filename)
                        ext = Path(file_path).suffix.lower()
                        
                        if ext in self.allowed_extensions:
                            # 파일 크기 체크
                            try:
                                if os.path.getsize(file_path) <= self.MAX_FILE_SIZE:
                                    files.append(file_path)
                                else:
                                    print(f"   ⚠️ 파일 크기 초과: {filename}")
                            except OSError:
                                continue
                            
            except PermissionError:
                continue
            except Exception as e:
                logger.warning(f"폴더 스캔 오류 ({folder}): {e}")
                continue
        
        logger.info(f"스캔 완료: {len(files)}개 파일 발견")
        return files
    
    def _get_file_category(self, file_path: str) -> str:
        """파일 카테고리 결정"""
        ext = Path(file_path).suffix.lower()
        for cat, exts in self.SUPPORTED_EXTENSIONS.items():
            if ext in exts:
                return cat
        return 'document'
    
    def _upload_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """파일을 서버에 업로드 (백엔드에서 파싱)"""
        try:
            file_name = Path(file_path).name
            file_category = self._get_file_category(file_path)
            
            with open(file_path, 'rb') as f:
                files = {
                    'file': (file_name, f, 'application/octet-stream')
                }
                data = {
                    'file_path': file_path,
                    'file_category': file_category
                }
                
                response = requests.post(
                    f"{API_BASE_URL}/api/v2/data-collection/client-file-upload/{self.user_id}",
                    headers={
                        "Authorization": f"Bearer {self.token}"
                    },
                    files=files,
                    data=data,
                    timeout=120  # 큰 파일은 파싱에 시간이 걸릴 수 있음
                )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('skipped'):
                    logger.debug(f"파일 스킵: {file_name} - {result.get('message')}")
                else:
                    logger.info(f"파일 업로드 성공: {file_name} ({result.get('chunks_count', 0)}개 청크)")
                return result
            else:
                logger.warning(f"파일 업로드 실패 ({file_name}): {response.status_code}")
                return None
                
        except Exception as e:
            logger.debug(f"파일 업로드 오류 ({file_path}): {e}")
            return None
    
    # =========================================================================
    # 브라우저 히스토리 수집 관련 메서드
    # =========================================================================
    
    def _get_browser_paths(self) -> Dict[str, str]:
        """현재 운영체제에 맞는 브라우저 히스토리 DB 경로를 반환"""
        system = platform.system()
        
        if system == 'Windows':
            return {
                'chrome': os.path.expanduser(r'~\AppData\Local\Google\Chrome\User Data\Default\History'),
                'edge': os.path.expanduser(r'~\AppData\Local\Microsoft\Edge\User Data\Default\History')
            }
        elif system == 'Darwin':  # macOS
            return {
                'chrome': os.path.expanduser('~/Library/Application Support/Google/Chrome/Default/History'),
                'edge': os.path.expanduser('~/Library/Application Support/Microsoft Edge/Default/History')
            }
        elif system == 'Linux':
            return {
                'chrome': os.path.expanduser('~/.config/google-chrome/Default/History'),
                'edge': os.path.expanduser('~/.config/microsoft-edge/Default/History')
            }
        
        return {}
    
    def _collect_browser_history(self) -> List[Dict[str, Any]]:
        """브라우저 히스토리를 수집"""
        all_history = []
        browser_paths = self._get_browser_paths()
        
        for browser_name, db_path in browser_paths.items():
            if not os.path.exists(db_path):
                print(f"   ⚠️ {browser_name} 히스토리 없음: {db_path}")
                continue
            
            history = self._get_browser_history(browser_name, db_path)
            if history:
                all_history.extend(history)
                print(f"   ✅ {browser_name}: {len(history)}개 항목 수집")
        
        return all_history
    
    def _get_browser_history(self, browser_name: str, db_path: str) -> List[Dict[str, Any]]:
        """특정 브라우저의 히스토리를 읽어옴"""
        history_data = []
        temp_path = f"{db_path}_jarvis_temp"
        
        try:
            # 브라우저가 DB를 잠그고 있을 수 있으므로 복사본 사용
            shutil.copy2(db_path, temp_path)
            
            conn = sqlite3.connect(temp_path)
            cursor = conn.cursor()
            
            # 최근 7일간의 히스토리만 가져옴
            seven_days_ago = datetime.now() - timedelta(days=7)
            # Chrome의 시간은 1601년 1월 1일 기준 마이크로초
            webkit_timestamp = int((seven_days_ago - datetime(1601, 1, 1)).total_seconds() * 1_000_000)
            
            query = """
                SELECT url, title, last_visit_time 
                FROM urls 
                WHERE last_visit_time > ? 
                ORDER BY last_visit_time DESC 
                LIMIT 200
            """
            
            cursor.execute(query, (webkit_timestamp,))
            rows = cursor.fetchall()
            
            for row in rows:
                url, title, visit_time = row
                
                # URL 필터링 (스킵할 패턴)
                if self._should_skip_url(url):
                    continue
                
                # WebKit 타임스탬프를 datetime으로 변환
                visit_datetime = datetime(1601, 1, 1) + timedelta(microseconds=visit_time)
                
                history_data.append({
                    'browser_name': browser_name,
                    'url': url,
                    'title': title or '',
                    'visit_time': visit_datetime.isoformat()
                })
            
            conn.close()
            
        except Exception as e:
            logger.warning(f"{browser_name} 히스토리 수집 오류: {e}")
        finally:
            # 임시 파일 삭제
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
        
        return history_data
    
    def _should_skip_url(self, url: str) -> bool:
        """스킵해야 할 URL인지 확인"""
        if not url:
            return True
        
        url_lower = url.lower()
        
        # 스킵할 URL 패턴
        skip_patterns = [
            # 로컬/내부 URL
            'localhost', '127.0.0.1', 'file://', 'chrome://', 'edge://', 'about:',
            # 소셜미디어 (대부분 SPA)
            'youtube.com', 'youtu.be', 'facebook.com', 'instagram.com',
            'twitter.com', 'x.com', 'tiktok.com', 'linkedin.com/feed',
            'reddit.com', 'discord.com', 'slack.com', 'telegram.org',
            # 검색 엔진 (검색 결과 페이지)
            'google.com/search', 'bing.com/search', 'naver.com/search',
            'duckduckgo.com', 'yahoo.com/search',
            # 인증/로그인 페이지
            'login', 'signin', 'signup', 'auth', 'oauth', 'sso',
            # 파일/스트리밍
            'drive.google.com', 'dropbox.com', 'onedrive.live',
            # 이메일
            'mail.google.com', 'outlook.live', 'mail.naver',
            # 기타
            'notion.so', 'figma.com', 'canva.com'
        ]
        
        return any(pattern in url_lower for pattern in skip_patterns)
    
    def _upload_browser_history(self, history: List[Dict[str, Any]]) -> bool:
        """브라우저 히스토리를 서버에 업로드"""
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/v2/data-collection/client-browser-history/{self.user_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json={"history": history},
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"브라우저 히스토리 업로드 성공: {result.get('saved_count', 0)}개 저장")
                return True
            else:
                logger.warning(f"브라우저 히스토리 업로드 실패: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"브라우저 히스토리 업로드 오류: {e}")
            return False
    
    # =========================================================================
    # 서버 통신 관련 메서드
    # =========================================================================
    
    def _update_server_status(self, progress: float, message: str, is_done: bool = False):
        """서버에 진행 상태 업데이트"""
        try:
            requests.post(
                f"{API_BASE_URL}/api/v2/data-collection/client-status/{self.user_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json={
                    "progress": progress,
                    "message": message,
                    "is_done": is_done
                },
                timeout=10
            )
        except Exception:
            pass  # 상태 업데이트 실패는 무시
    
    def _notify_completion(self):
        """서버에 수집 완료 알림"""
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/v2/data-collection/client-complete/{self.user_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                timeout=30
            )
            
            if response.status_code == 200:
                logger.info("서버에 수집 완료 알림 전송됨")
            else:
                logger.warning(f"수집 완료 알림 실패: {response.status_code}")
                
        except Exception as e:
            logger.error(f"수집 완료 알림 오류: {e}")
