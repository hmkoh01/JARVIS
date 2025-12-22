"""
JARVIS Client-Side Data Collector
로컬에서 파일을 스캔하고 백엔드로 업로드합니다.

백엔드에서 DocumentParser(Docling)를 사용하여 파싱합니다.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from threading import Event

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
    
    로컬에서 파일을 스캔하고 백엔드로 업로드합니다.
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
        """데이터 수집 실행"""
        print(f"🔄 ClientDataCollector.run() 시작 - folders: {self.selected_folders}")
        print(f"   user_id: {self.user_id}, token: {self.token[:20]}...")
        
        try:
            self.progress_updated.emit(0.0, "📁 파일 스캔 시작...")
            
            # 1. 파일 스캔
            print("📁 파일 스캔 중...")
            files_to_process = self._scan_files()
            print(f"📁 스캔 완료: {len(files_to_process)}개 파일 발견")
            
            if self._stop_event.is_set():
                print("⏹️ 수집 중지됨")
                return
            
            if not files_to_process:
                print("⚠️ 수집할 파일이 없음")
                self.progress_updated.emit(100.0, "⚠️ 수집할 파일이 없습니다.")
                self._notify_completion()
                self.collection_completed.emit()
                return
            
            total_files = len(files_to_process)
            self.progress_updated.emit(5.0, f"📄 {total_files}개 파일 발견")
            
            # 2. 파일 업로드 (하나씩 업로드)
            processed_count = 0
            success_count = 0
            skipped_count = 0
            
            for file_path in files_to_process:
                if self._stop_event.is_set():
                    return
                
                result = self._upload_file(file_path)
                processed_count += 1
                
                if result:
                    if result.get('skipped'):
                        skipped_count += 1
                    else:
                        success_count += 1
                        self.file_processed.emit(Path(file_path).name)
                
                progress = 5.0 + (processed_count / total_files) * 85.0
                self.progress_updated.emit(
                    progress, 
                    f"📤 업로드 중... ({processed_count}/{total_files})"
                )
            
            # 3. 완료 알림
            self.progress_updated.emit(95.0, "📤 서버에 완료 알림...")
            self._notify_completion()
            
            message = f"✅ 완료! {success_count}개 처리, {skipped_count}개 스킵"
            self.progress_updated.emit(100.0, message)
            print(message)
            self.collection_completed.emit()
            
        except Exception as e:
            logger.error(f"데이터 수집 오류: {e}", exc_info=True)
            self.collection_error.emit(f"수집 오류: {str(e)}")
    
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
