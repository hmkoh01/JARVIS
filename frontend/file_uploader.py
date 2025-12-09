#!/usr/bin/env python3
"""
File Uploader Module for JARVIS exe
로컬 파일을 수집하여 VPS 백엔드로 업로드하는 모듈
"""

import os
import sys
import threading
import queue
import time
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional, Callable
from dataclasses import dataclass

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)


@dataclass
class UploadProgress:
    """업로드 진행 상황"""
    total_files: int = 0
    processed_files: int = 0
    uploaded_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    current_file: str = ""
    is_done: bool = False
    error_message: str = ""
    
    @property
    def progress_percent(self) -> float:
        if self.total_files == 0:
            return 0.0
        return (self.processed_files / self.total_files) * 100


class FileUploader:
    """
    로컬 파일을 VPS 백엔드로 업로드하는 클래스
    """
    
    # 지원하는 파일 확장자
    SUPPORTED_EXTENSIONS = {
        'document': ['.txt', '.doc', '.docx', '.pdf', '.md', '.rtf', '.odt', '.tex'],
        'spreadsheet': ['.xls', '.xlsx', '.csv', '.ods', '.tsv'],
        'presentation': ['.ppt', '.pptx', '.odp', '.key'],
        'code': ['.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss', '.java', '.cpp', '.c', '.h', 
                 '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.r', '.m', '.sh', '.bat', '.ps1',
                 '.sql', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf'],
        'note': ['.note', '.notes', '.evernote', '.onenote'],
        'ebook': ['.epub', '.mobi', '.azw', '.azw3'],
    }
    
    # 스킵할 디렉토리 패턴
    SKIP_DIRECTORIES = {
        'Windows', 'Program Files', 'Program Files (x86)', '$Recycle.Bin', 
        '.git', 'node_modules', '__pycache__', 'AppData', '.venv', 'venv',
        '.idea', '.vscode', 'dist', 'build', '__MACOSX', '.Trash'
    }
    
    # 최대 파일 크기 (50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024
    
    def __init__(self, user_id: int, api_base_url: str):
        self.user_id = user_id
        self.api_base_url = api_base_url.rstrip('/')
        
        # 허용된 확장자 집합
        self.allowed_extensions: Set[str] = set()
        for exts in self.SUPPORTED_EXTENSIONS.values():
            self.allowed_extensions.update(exts)
        
        # 진행 상황
        self.progress = UploadProgress()
        
        # 백그라운드 스레드 제어
        self._stop_event = threading.Event()
        self._upload_thread: Optional[threading.Thread] = None
        
        # 콜백
        self._progress_callback: Optional[Callable[[UploadProgress], None]] = None
    
    def get_file_category(self, file_path: str) -> str:
        """파일 카테고리를 반환합니다."""
        ext = Path(file_path).suffix.lower()
        for category, extensions in self.SUPPORTED_EXTENSIONS.items():
            if ext in extensions:
                return category
        return 'other'
    
    def should_skip_directory(self, dir_path: str) -> bool:
        """스킵해야 할 디렉토리인지 확인합니다."""
        path_parts = Path(dir_path).parts
        return any(part in self.SKIP_DIRECTORIES for part in path_parts)
    
    def collect_files(self, folders: List[str]) -> List[Dict]:
        """
        지정된 폴더들에서 지원되는 파일들을 수집합니다.
        
        Args:
            folders: 스캔할 폴더 경로 리스트
            
        Returns:
            파일 정보 딕셔너리 리스트
        """
        collected_files = []
        seen_paths: Set[str] = set()
        
        for folder in folders:
            folder_path = Path(folder)
            
            if not folder_path.exists():
                logger.warning(f"폴더가 존재하지 않습니다: {folder}")
                continue
            
            if folder_path.is_file():
                # 단일 파일인 경우
                file_path = str(folder_path)
                ext = folder_path.suffix.lower()
                
                if ext in self.allowed_extensions and file_path not in seen_paths:
                    try:
                        stat = os.stat(file_path)
                        if stat.st_size <= self.MAX_FILE_SIZE:
                            collected_files.append({
                                'path': file_path,
                                'name': folder_path.name,
                                'size': stat.st_size,
                                'category': self.get_file_category(file_path)
                            })
                            seen_paths.add(file_path)
                    except (PermissionError, OSError) as e:
                        logger.warning(f"파일 접근 오류: {file_path} - {e}")
                continue
            
            # 폴더 스캔
            try:
                for root, dirs, files in os.walk(folder_path):
                    # 스킵할 디렉토리 제외
                    if self.should_skip_directory(root):
                        dirs.clear()  # 하위 디렉토리 탐색 중단
                        continue
                    
                    # 숨김 폴더 제외
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    
                    for filename in files:
                        # 숨김 파일 제외
                        if filename.startswith('.') or filename.startswith('~$'):
                            continue
                        
                        file_path = os.path.join(root, filename)
                        ext = Path(filename).suffix.lower()
                        
                        if ext not in self.allowed_extensions:
                            continue
                        
                        if file_path in seen_paths:
                            continue
                        
                        try:
                            stat = os.stat(file_path)
                            
                            # 파일 크기 제한
                            if stat.st_size > self.MAX_FILE_SIZE:
                                logger.info(f"파일 크기 초과, 스킵: {filename} ({stat.st_size / 1024 / 1024:.1f}MB)")
                                continue
                            
                            # 빈 파일 스킵
                            if stat.st_size == 0:
                                continue
                            
                            collected_files.append({
                                'path': file_path,
                                'name': filename,
                                'size': stat.st_size,
                                'category': self.get_file_category(file_path)
                            })
                            seen_paths.add(file_path)
                            
                        except (PermissionError, OSError) as e:
                            logger.warning(f"파일 접근 오류: {file_path} - {e}")
                            
            except PermissionError as e:
                logger.warning(f"폴더 접근 오류: {folder} - {e}")
        
        logger.info(f"📊 파일 수집 완료: {len(collected_files)}개 파일")
        return collected_files
    
    def upload_file(self, file_info: Dict) -> Dict:
        """
        단일 파일을 업로드합니다.
        
        Args:
            file_info: 파일 정보 딕셔너리
            
        Returns:
            업로드 결과 딕셔너리
        """
        import requests
        
        file_path = file_info['path']
        
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            files = {
                'file': (file_info['name'], content)
            }
            data = {
                'original_path': file_path,
                'file_category': file_info['category']
            }
            
            response = requests.post(
                f"{self.api_base_url}/api/v2/files/upload/{self.user_id}",
                files=files,
                data=data,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'success': True,
                    'filename': file_info['name'],
                    'skipped': result.get('skipped', False),
                    'chunks': result.get('chunks', 0),
                    'message': result.get('message', '')
                }
            else:
                return {
                    'success': False,
                    'filename': file_info['name'],
                    'error': f"HTTP {response.status_code}: {response.text}"
                }
                
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'filename': file_info['name'],
                'error': "업로드 타임아웃"
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'filename': file_info['name'],
                'error': "서버 연결 실패"
            }
        except Exception as e:
            return {
                'success': False,
                'filename': file_info['name'],
                'error': str(e)
            }
    
    def _upload_worker(self, files: List[Dict]):
        """백그라운드에서 파일을 업로드하는 워커"""
        self.progress = UploadProgress(total_files=len(files))
        
        if not files:
            self.progress.is_done = True
            self._notify_progress()
            return
        
        for file_info in files:
            if self._stop_event.is_set():
                self.progress.error_message = "업로드가 중단되었습니다."
                break
            
            self.progress.current_file = file_info['name']
            self._notify_progress()
            
            result = self.upload_file(file_info)
            
            self.progress.processed_files += 1
            
            if result['success']:
                if result.get('skipped'):
                    self.progress.skipped_files += 1
                else:
                    self.progress.uploaded_files += 1
            else:
                self.progress.failed_files += 1
                logger.warning(f"업로드 실패: {result['filename']} - {result.get('error', 'Unknown')}")
            
            self._notify_progress()
            
            # API 부하 방지를 위한 약간의 지연
            time.sleep(0.1)
        
        self.progress.is_done = True
        self.progress.current_file = ""
        self._notify_progress()
        
        logger.info(
            f"📤 업로드 완료 - 성공: {self.progress.uploaded_files}, "
            f"스킵: {self.progress.skipped_files}, 실패: {self.progress.failed_files}"
        )
    
    def _notify_progress(self):
        """진행 상황 콜백 호출"""
        if self._progress_callback:
            try:
                self._progress_callback(self.progress)
            except Exception as e:
                logger.warning(f"진행 상황 콜백 오류: {e}")
    
    def start_upload(
        self, 
        folders: List[str],
        progress_callback: Optional[Callable[[UploadProgress], None]] = None
    ):
        """
        파일 수집 및 업로드를 시작합니다.
        
        Args:
            folders: 스캔할 폴더 리스트
            progress_callback: 진행 상황 콜백 함수
        """
        self._progress_callback = progress_callback
        self._stop_event.clear()
        
        # 파일 수집
        logger.info(f"📂 파일 수집 시작: {len(folders)}개 폴더")
        files = self.collect_files(folders)
        
        if not files:
            self.progress = UploadProgress(is_done=True)
            self.progress.error_message = "업로드할 파일이 없습니다."
            self._notify_progress()
            return
        
        # 백그라운드에서 업로드 시작
        self._upload_thread = threading.Thread(
            target=self._upload_worker,
            args=(files,),
            daemon=True
        )
        self._upload_thread.start()
    
    def stop_upload(self):
        """업로드를 중단합니다."""
        self._stop_event.set()
        if self._upload_thread:
            self._upload_thread.join(timeout=5)
    
    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """
        업로드 완료를 대기합니다.
        
        Args:
            timeout: 최대 대기 시간 (초)
            
        Returns:
            완료 여부
        """
        if self._upload_thread:
            self._upload_thread.join(timeout=timeout)
            return self.progress.is_done
        return True


def upload_files_from_folders(
    user_id: int,
    api_base_url: str,
    folders: List[str],
    progress_callback: Optional[Callable[[UploadProgress], None]] = None
) -> UploadProgress:
    """
    폴더에서 파일을 수집하여 업로드합니다. (편의 함수)
    
    Args:
        user_id: 사용자 ID
        api_base_url: API 기본 URL
        folders: 스캔할 폴더 리스트
        progress_callback: 진행 상황 콜백
        
    Returns:
        최종 업로드 진행 상황
    """
    uploader = FileUploader(user_id, api_base_url)
    uploader.start_upload(folders, progress_callback)
    uploader.wait_for_completion()
    return uploader.progress


if __name__ == "__main__":
    # 테스트
    def print_progress(progress: UploadProgress):
        print(f"\r[{progress.progress_percent:.1f}%] "
              f"처리: {progress.processed_files}/{progress.total_files} "
              f"현재: {progress.current_file[:30] if progress.current_file else '-'}", end="")
        if progress.is_done:
            print(f"\n완료! 성공: {progress.uploaded_files}, 스킵: {progress.skipped_files}, 실패: {progress.failed_files}")
    
    # 테스트 실행
    result = upload_files_from_folders(
        user_id=1,
        api_base_url="http://158.247.197.192:8000",
        folders=[str(Path.home() / "Documents")],
        progress_callback=print_progress
    )

