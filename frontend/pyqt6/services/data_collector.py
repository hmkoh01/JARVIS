"""
JARVIS Client-Side Data Collector
로컬에서 파일을 스캔하고 파싱한 후 백엔드로 업로드합니다.

원격 서버 환경에서 사용자의 로컬 파일을 수집하기 위한 클라이언트 측 구현.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from threading import Thread, Event

import requests
from PyQt6.QtCore import QObject, pyqtSignal, QThread

try:
    from config import API_BASE_URL
except ImportError:
    API_BASE_URL = "http://localhost:8000"

logger = logging.getLogger(__name__)


class ClientDataCollector(QThread):
    """
    클라이언트 측 데이터 수집 워커.
    
    로컬에서 파일을 스캔하고 텍스트를 추출한 후 백엔드로 업로드합니다.
    
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
    
    # 지원하는 파일 확장자
    SUPPORTED_EXTENSIONS = {
        'document': ['.txt', '.md', '.rtf'],
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
        try:
            self.progress_updated.emit(0.0, "📁 파일 스캔 시작...")
            
            # 1. 파일 스캔
            files_to_process = self._scan_files()
            
            if self._stop_event.is_set():
                return
            
            if not files_to_process:
                self.progress_updated.emit(100.0, "⚠️ 수집할 파일이 없습니다.")
                self._notify_completion()
                self.collection_completed.emit()
                return
            
            total_files = len(files_to_process)
            self.progress_updated.emit(10.0, f"📄 {total_files}개 파일 발견")
            
            # 2. 파일 파싱 및 업로드 (배치 처리)
            batch_size = 10
            processed_count = 0
            
            for i in range(0, total_files, batch_size):
                if self._stop_event.is_set():
                    return
                
                batch = files_to_process[i:i + batch_size]
                batch_data = []
                
                for file_path in batch:
                    if self._stop_event.is_set():
                        return
                    
                    file_data = self._process_file(file_path)
                    if file_data:
                        batch_data.append(file_data)
                        self.file_processed.emit(file_data['file_name'])
                    
                    processed_count += 1
                    progress = 10.0 + (processed_count / total_files) * 70.0
                    self.progress_updated.emit(
                        progress, 
                        f"📄 처리 중... ({processed_count}/{total_files})"
                    )
                
                # 배치 업로드
                if batch_data:
                    self._upload_batch(batch_data)
            
            # 3. 완료 알림
            self.progress_updated.emit(95.0, "📤 서버에 완료 알림...")
            self._notify_completion()
            
            self.progress_updated.emit(100.0, "✅ 데이터 수집 완료!")
            self.collection_completed.emit()
            
        except Exception as e:
            logger.error(f"데이터 수집 오류: {e}", exc_info=True)
            self.collection_error.emit(f"수집 오류: {str(e)}")
    
    def _scan_files(self) -> List[str]:
        """선택된 폴더에서 파일 목록 스캔"""
        files = []
        
        for folder in self.selected_folders:
            if self._stop_event.is_set():
                break
            
            folder_path = Path(folder)
            if not folder_path.exists() or not folder_path.is_dir():
                continue
            
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
                            files.append(file_path)
                            
            except PermissionError:
                continue
            except Exception as e:
                logger.warning(f"폴더 스캔 오류 ({folder}): {e}")
                continue
        
        logger.info(f"스캔 완료: {len(files)}개 파일 발견")
        return files
    
    def _process_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """파일을 파싱하고 청크로 분할"""
        try:
            # 파일 해시 계산
            with open(file_path, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()
            
            # 텍스트 추출
            text = self._extract_text(file_path)
            if not text or len(text.strip()) < 50:
                return None
            
            # 청크 분할
            chunks = self._chunk_text(text)
            if not chunks:
                return None
            
            # 파일 카테고리 결정
            ext = Path(file_path).suffix.lower()
            category = 'document'
            for cat, exts in self.SUPPORTED_EXTENSIONS.items():
                if ext in exts:
                    category = cat
                    break
            
            return {
                'file_path': file_path,
                'file_name': Path(file_path).name,
                'file_category': category,
                'file_hash': file_hash,
                'chunks': [
                    {'text': chunk, 'snippet': chunk[:200]}
                    for chunk in chunks
                ]
            }
            
        except Exception as e:
            logger.debug(f"파일 처리 오류 ({file_path}): {e}")
            return None
    
    def _extract_text(self, file_path: str) -> Optional[str]:
        """파일에서 텍스트 추출 (간단한 구현)"""
        try:
            ext = Path(file_path).suffix.lower()
            
            # 텍스트 파일
            if ext in ['.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', 
                      '.html', '.css', '.scss', '.java', '.cpp', '.c', '.h',
                      '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt',
                      '.r', '.sh', '.bat', '.ps1', '.sql', '.json', '.xml',
                      '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.rtf']:
                
                # 다양한 인코딩 시도
                for encoding in ['utf-8', 'utf-16', 'cp949', 'euc-kr', 'latin-1']:
                    try:
                        with open(file_path, 'r', encoding=encoding) as f:
                            content = f.read()
                            # RTF의 경우 기본 텍스트만 추출
                            if ext == '.rtf':
                                content = self._strip_rtf(content)
                            return content
                    except UnicodeDecodeError:
                        continue
                
            return None
            
        except Exception as e:
            logger.debug(f"텍스트 추출 오류 ({file_path}): {e}")
            return None
    
    def _strip_rtf(self, rtf_text: str) -> str:
        """간단한 RTF 태그 제거"""
        import re
        # RTF 컨트롤 워드 및 그룹 제거
        text = re.sub(r'\\[a-z]+\d*\s?', '', rtf_text)
        text = re.sub(r'[{}]', '', text)
        return text
    
    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """텍스트를 청크로 분할"""
        if not text:
            return []
        
        text = text.strip()
        if len(text) <= chunk_size:
            return [text]
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            if end < len(text):
                # 문장 경계에서 분할 시도
                boundary = text.rfind('.', start + chunk_size - 100, end)
                if boundary == -1:
                    boundary = text.rfind(' ', start + chunk_size - 100, end)
                if boundary > start:
                    end = boundary + 1
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            start = end - overlap
        
        return chunks
    
    def _upload_batch(self, batch_data: List[Dict[str, Any]]) -> bool:
        """배치 데이터를 서버에 업로드"""
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/v2/data-collection/client-upload/{self.user_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json={"files": batch_data},
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"배치 업로드 성공: {result.get('processed_files', 0)}개 파일")
                return True
            else:
                logger.warning(f"배치 업로드 실패: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"배치 업로드 오류: {e}")
            return False
    
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

