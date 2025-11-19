#!/usr/bin/env python3
import os
import sys
from pathlib import Path
import aiohttp
from bs4 import BeautifulSoup
import shutil
import time
import json
import sqlite3
import psutil
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import hashlib
from PIL import ImageGrab
import platform

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.settings import settings
from .repository import Repository
from .sqlite_meta import SQLiteMeta
from .document_parser import DocumentParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder

logger = logging.getLogger(__name__)

def init_worker_logging():
    """
    ProcessPoolExecutor 워커의 로깅을 완전히 억제하여
    불필요한 INFO 로그(모듈 초기화 등) 스팸을 방지합니다.
    """
    # 워커 프로세스의 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    
    # 기존의 모든 핸들러 제거 (중복 로그 방지)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # WARNING 레벨의 간단한 콘솔 핸들러만 추가
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    root_logger.addHandler(console_handler)
    
    # 자식 로거들도 WARNING 레벨로 설정
    for logger_name in ['backend.config.logging_config', '__main__']:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

# -----------------------------------------------------------------------------
# FileCollector
# -----------------------------------------------------------------------------
class FileCollector:
    """사용자 드라이브의 파일들을 수집하는 클래스"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.logger = logger.getChild(f"FileCollector[user={user_id}]")
        self.sqlite_meta = SQLiteMeta()
        self.supported_extensions = {
            'document': ['.txt', '.doc', '.docx', '.pdf', '.md', '.rtf', '.odt', '.tex'],
            'spreadsheet': ['.xls', '.xlsx', '.csv', '.ods', '.tsv'],
            'presentation': ['.ppt', '.pptx', '.odp', '.key'],
            'code': ['.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss', '.java', '.cpp', '.c', '.h', 
                     '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.r', '.m', '.sh', '.bat', '.ps1',
                     '.sql', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf'],
            'note': ['.note', '.notes', '.evernote', '.onenote'],
            'ebook': ['.epub', '.mobi', '.azw', '.azw3'],
        }
        self.allowed_extensions = {ext for exts in self.supported_extensions.values() for ext in exts}

    def _get_directory_size(self, path: str) -> int:
        """재귀적으로 디렉토리의 전체 크기를 계산합니다."""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                        except (OSError, FileNotFoundError):
                            continue
        except PermissionError:
            return 0
        return total_size

    def _format_size(self, size_bytes: int) -> str:
        """바이트를 읽기 좋은 형태(KB, MB, GB)로 변환합니다."""
        if size_bytes == 0:
            return "(0 bytes)"
        power = 1024
        n = 0
        power_labels = {0: 'bytes', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size_bytes >= power and n < len(power_labels) - 1:
            size_bytes /= power
            n += 1
        return f"({size_bytes:.1f} {power_labels[n]})"

    def get_file_category(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        for category, extensions in self.supported_extensions.items():
            if ext in extensions: return category
        return 'other'

    def should_skip_directory(self, dir_path: str) -> bool:
        skip_patterns = ['Windows', 'Program Files', '$Recycle.Bin', '.git', 'node_modules', '__pycache__', 'AppData']
        return any(part in Path(dir_path).parts for part in skip_patterns)

    def calculate_file_hash(self, file_path: str) -> str:
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                hash_md5.update(f.read(1024 * 1024))
            return hash_md5.hexdigest()
        except: return f"error_{int(time.time())}"

    def is_file_modified(self, file_path: str, last_modified: datetime) -> bool:
        stored_modified = self.sqlite_meta.get_file_last_modified(file_path)
        return stored_modified is None or last_modified > stored_modified

    def get_user_folders(self, calculate_size: bool = True) -> List[Dict[str, Any]]:
        """사용자 홈 디렉토리의 모든 폴더를 스캔하고 크기를 계산하여 반환합니다."""
        folders = []
        base_path = os.path.join(os.path.expanduser("~"), "Desktop")
        
        try:
            with os.scandir(base_path) as it:
                for entry in it:
                    if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith('.'):
                        if self.should_skip_directory(entry.path):
                            continue
                        try:
                            stat = entry.stat()
                            
                            if calculate_size:
                                dir_size = self._get_directory_size(entry.path)
                                size_formatted = self._format_size(dir_size) if dir_size is not None else "(크기 계산 실패)"
                            else:
                                dir_size = None
                                size_formatted = "(크기 미계산)"

                            folders.append({
                                'name': entry.name,
                                'path': entry.path,
                                'size_formatted': size_formatted,
                                'modified_date': datetime.fromtimestamp(stat.st_mtime)
                            })
                        except (OSError, PermissionError):
                            continue
        except Exception as e:
            self.logger.error("사용자 폴더를 읽는 중 오류 발생: %s", e, exc_info=True)

        return sorted(folders, key=lambda x: x['name'].lower())

    def collect_files_from_drive(
        self,
        incremental: bool,
        manager: 'DataCollectionManager',
        selected_folders: Optional[List[str]],
        progress_bounds: Tuple[float, float] = (0.0, 50.0)
    ) -> List[Dict[str, Any]]:
        paths_to_scan = []
        if selected_folders is None:
            # "전체 사용자 폴더 스캔"이 선택된 경우, 기본 폴더 목록을 가져옵니다.
            default_folders = self.get_user_folders()
            paths_to_scan = [folder['path'] for folder in default_folders]
        else:
            paths_to_scan = selected_folders
        
        if not paths_to_scan: 
            self.logger.warning("⚠️ 스캔할 폴더가 없습니다.")
            return []

        collected_files = []
        total_scanned = 0
        skipped_by_extension = 0
        skipped_by_hash = 0
        
        progress_start, progress_end = progress_bounds
        progress_range = max(progress_end - progress_start, 0.0)

        if manager:
            manager.progress = progress_start

        total_paths = len(paths_to_scan)

        for i, folder_path in enumerate(paths_to_scan):
            # 경로를 운영체제에 맞게 정규화하여 경로 구분자 문제를 해결합니다.
            normalized_path = os.path.normpath(folder_path)
            
            if manager and total_paths > 0: 
                # progress_start ~ progress_end 범위에서 진행률 계산
                manager.progress = progress_start + ((i + 1) / total_paths) * progress_range
                # 정규화된 경로를 사용해 폴더 이름을 가져옵니다.
                manager.progress_message = f"📁 스캔 중: {Path(normalized_path).name}"
            
            try:
                # os.walk에 정규화된 경로를 전달합니다.
                for root, dirs, files in os.walk(normalized_path):
                    dirs[:] = [d for d in dirs if not self.should_skip_directory(os.path.join(root, d))]
                    for file in files:
                        try:
                            total_scanned += 1
                            file_path = os.path.join(root, file)
                            file_ext = Path(file_path).suffix.lower()
                            
                            if file.startswith("~$"):
                                continue
                            
                            if file_ext not in self.allowed_extensions:
                                skipped_by_extension += 1
                                continue
                            
                            stat = os.stat(file_path)
                            modified_date = datetime.fromtimestamp(stat.st_mtime)
                            if incremental and not self.is_file_modified(file_path, modified_date): continue
                            file_hash = self.calculate_file_hash(file_path)
                            
                            if self.sqlite_meta.is_file_hash_exists(file_hash):
                                skipped_by_hash += 1
                                continue
                            
                            collected_files.append({
                                'user_id': self.user_id,
                                'file_path': file_path,
                                'file_name': file,
                                'file_size': stat.st_size,
                                'file_type': file_ext,  # 파일 확장자
                                'file_category': self.get_file_category(file_path),  # 파일 카테고리
                                'file_hash': file_hash,
                                'modified_date': modified_date,
                                'created_date': datetime.fromtimestamp(stat.st_ctime),
                                'accessed_date': datetime.fromtimestamp(stat.st_atime)
                            })
                        except (PermissionError, OSError, FileNotFoundError): continue
            except Exception as e: 
                self.logger.error("폴더 스캔 오류 %s: %s", normalized_path, e, exc_info=True)
        
        # 파일 수집 완료 시 progress_end로 설정
        if manager:
            if total_paths > 0:
                manager.progress = progress_end
            else:
                manager.progress = progress_start
        
        # 수집 결과 로깅
        self.logger.info("📊 파일 수집 결과 - 총 스캔: %d, 확장자 제외: %d, 중복 제외: %d, 신규 파일: %d",
                         total_scanned, skipped_by_extension, skipped_by_hash, len(collected_files))
        
        if len(collected_files) == 0 and total_scanned > 0:
            self.logger.warning("⚠️ 지원되는 확장자 목록: %s", ', '.join(sorted(self.allowed_extensions)))
        
        return collected_files

    def save_files_to_db(
        self,
        files: List[Dict[str, Any]],
        repo: Repository,
        embedder: 'BGEM3Embedder',
        parser: DocumentParser,
        manager: Optional['DataCollectionManager'] = None
    ) -> int:
        if not files:
            self.logger.warning("⚠️ 저장할 파일이 없습니다.")
            return 0
        if not repo:
            self.logger.error("⚠️ Repository가 초기화되지 않았습니다.")
            return 0
            
        saved_count, text_files = 0, []
        try:
            self.sqlite_meta.conn.execute("BEGIN TRANSACTION")
            for file_info in files:
                if self.sqlite_meta.insert_collected_file(file_info):
                    saved_count += 1
                    if file_info['file_category'] in ['document', 'spreadsheet', 'presentation', 'code', 'note']:
                        text_files.append(file_info)
            self.sqlite_meta.conn.commit()
            self.logger.info("✅ SQLite 파일 메타데이터 저장: %d개, 텍스트 인덱싱 대상: %d개",
                             saved_count, len(text_files))
        except Exception as e: 
            self.sqlite_meta.conn.rollback()
            self.logger.error("❌ SQLite 파일 저장 실패: %s", e, exc_info=True)
            return 0
        
        if text_files:
            self._batch_index_text_files(text_files, repo, embedder, parser, manager)
        else:
            self.logger.warning("⚠️ 텍스트 인덱싱 대상 파일이 없습니다.")
        return saved_count

    def _extract_and_save_entities(self, text: str, source_id: str, source_type: str):
        """텍스트에서 엔티티를 추출하고 저장"""
        try:
            import re
            # 1. 이메일 추출
            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', text)
            for email in emails:
                entity_id = self.sqlite_meta.upsert_entity(email, 'Person')
                if entity_id > 0:
                    self.sqlite_meta.add_entity_relation(entity_id, source_id, source_type, 'mentioned_in')

            # 2. 주요 기술 키워드 (예시)
            tech_keywords = ['Python', 'Java', 'JavaScript', 'TypeScript', 'React', 'Vue', 'FastAPI', 
                           'Django', 'Spring', 'Docker', 'Kubernetes', 'AWS', 'Azure', 'GCP', 'SQL', 'NoSQL']
            for kw in tech_keywords:
                if kw.lower() in text.lower():
                    entity_id = self.sqlite_meta.upsert_entity(kw, 'Technology')
                    if entity_id > 0:
                        self.sqlite_meta.add_entity_relation(entity_id, source_id, source_type, 'mentioned_in')
        except Exception as e:
            self.logger.warning(f"엔티티 추출 중 오류: {e}")

    @staticmethod
    def _parse_single_file(file_info: Dict[str, Any], parser_ref: Any, user_id: int):
        """(헬퍼 함수) 단일 파일을 파싱. ProcessPoolExecutor에서 실행됨."""
        try:
            parser = None
            if parser_ref is not None:
                if isinstance(parser_ref, type):
                    parser = parser_ref()
                else:
                    parser = parser_ref
            if parser is None or not hasattr(parser, "parse_and_chunk"):
                from .document_parser import DocumentParser as _DocumentParser
                parser = _DocumentParser()

            try:
                chunk_infos = parser.parse_and_chunk(file_info['file_path'])
            except RuntimeError as e:
                return None, file_info['file_name'], f"Docling RuntimeError: {e}"
            except Exception as e:
                return None, file_info['file_name'], f"Parsing Exception: {e}"
            if not chunk_infos:
                return None, file_info['file_name'], "청크 없음"

            doc_id = f"file_{hashlib.md5(file_info['file_path'].encode()).hexdigest()}"
            file_hash = file_info.get('file_hash', '')

            texts = []
            metas = []
            for chunk in chunk_infos:
                texts.append(chunk['text'])
                metas.append({
                    'user_id': user_id,
                    'source': 'file',
                    'path': file_info['file_path'],
                    'doc_id': doc_id,
                    'chunk_id': chunk['chunk_id'],
                    'snippet': chunk['snippet'],
                    'content': chunk['text']
                })

            return (texts, metas, file_hash, file_info['file_path'], len(chunk_infos)), file_info['file_name'], None
        # [수정] 자식 프로세스에서 발생 가능한 모든 오류를 잡기 위해 BaseException 사용
        except BaseException as e:
            return None, file_info['file_name'], f"Worker setup error: {e}"

    def _process_and_upload_batch(
        self,
        repo: Repository,
        embedder: 'BGEM3Embedder',
        texts: List[str],
        metas: List[Dict[str, Any]],
        batch_size: int
    ):
        """청크 배치를 받아 임베딩하고 Qdrant에 업로드합니다."""
        if not texts:
            return

        self.logger.info(
            "🧠 청크 %d개 배치 임베딩 및 업로드 중... (Embedding Batch Size: %d)",
            len(texts),
            batch_size
        )
        try:
            embeddings = embedder.encode_documents(texts, batch_size=batch_size)
            dense_vectors = embeddings['dense_vecs'].tolist()
            sparse_vectors = [
                embedder.convert_sparse_to_qdrant_format(lw)
                for lw in embeddings['lexical_weights']
            ]

            if repo.qdrant.upsert_vectors(metas, dense_vectors, sparse_vectors):
                self.logger.info("   ... ✅ Qdrant 업로드 성공: %d개", len(texts))
            else:
                self.logger.error("   ... ❌ Qdrant 업로드 실패")
        except Exception as e:
            # MemoryError 등 치명적 오류가 발생할 수 있으므로 상세 로그
            self.logger.error("   ... ❌ 임베딩/업로드 중 치명적 오류: %s", e, exc_info=True)

    def _batch_index_text_files(
        self,
        text_files: List[Dict[str, Any]],
        repo: Repository,
        embedder: 'BGEM3Embedder',
        parser: DocumentParser,
        manager: Optional['DataCollectionManager'] = None
    ):
        # 중복 파일 경로 제거 (동일 파일은 한 번만 파싱)
        seen_paths = set()
        unique_text_files: List[Dict[str, Any]] = []
        duplicate_count = 0

        for file_info in text_files:
            file_path = file_info.get('file_path')
            if not file_path:
                self.logger.debug("텍스트 인덱싱 대상에서 file_path가 없는 항목을 건너뜁니다: %s", file_info)
                continue

            normalized_path = os.path.normcase(os.path.abspath(file_path))
            if normalized_path in seen_paths:
                duplicate_count += 1
                continue

            seen_paths.add(normalized_path)
            unique_text_files.append(file_info)

        if duplicate_count:
            self.logger.debug("텍스트 인덱싱 대상에서 중복 파일 %d개를 제외했습니다.", duplicate_count)

        text_files = unique_text_files

        cpu_count = multiprocessing.cpu_count()
        self.logger.info(
            "📝 텍스트 파일 인덱싱 시작 - 파일 %d개, 사용 코어 %d개",
            len(text_files),
            cpu_count
        )
        
        # 진행률 업데이트 (파싱 시작)
        if manager:
            manager.progress_message = f"📄 파일 파싱 중... (총 {len(text_files)}개)"

        # 1. 환경에 따라 다른 처리 전략 설정
        is_gpu_available = getattr(embedder, "device", "cpu") == "cuda"
        embedding_batch_size = 128 if is_gpu_available else 32
        cpu_micro_batch_threshold = 5000  # CPU 모드에서 RAM 보호용 임계값

        all_texts: List[str] = []
        all_metas: List[Dict[str, Any]] = []
        total_chunk_count = 0
        parsed_count = 0
        failed_count = 0
        file_hash_map: Dict[str, str] = {}

        # --- 1. 파싱 (Parsing) ---
        # GPU 여부와 관계없이, 파싱은 항상 ProcessPoolExecutor로 병렬 처리합니다.

        max_workers = min(cpu_count, 8) if cpu_count > 0 else 1
        parser_ref = parser.__class__ if parser is not None else DocumentParser

        self.logger.info("--- [1/2] 파일 파싱 시작 (병렬 처리) ---")
        with ProcessPoolExecutor(max_workers=max_workers, initializer=init_worker_logging) as executor:
            futures = {
                executor.submit(self._parse_single_file, file_info, parser_ref, self.user_id): file_info
                for file_info in text_files
            }

            total_files = len(text_files)
            completed_files = 0
            
            for future in as_completed(futures):
                result, file_name, error = future.result()
                completed_files += 1
                
                if result:
                    texts, metas, file_hash, file_path, chunk_count = result

                    # 모든 결과를 RAM의 단일 리스트로 수집
                    all_texts.extend(texts)
                    all_metas.extend(metas)

                    total_chunk_count += len(texts)
                    if file_hash:
                        file_hash_map[file_path] = file_hash # file_path를 키로 사용
                    parsed_count += 1
                    self.logger.info("   ✓ %s: %d개 청크 (파싱 완료)", file_name, chunk_count)
                    
                    # [New] 첫 번째 청크에서 엔티티 추출 (성능을 위해 일부만)
                    if texts:
                        self._extract_and_save_entities(texts[0][:2000], file_path, 'file')

                else:
                    failed_count += 1
                    self.logger.warning("   ✗ 파일 파싱 오류 %s: %s", file_name, error)
                
                # 진행률 업데이트 (매 파일마다)
                if manager and total_files > 0:
                    manager.progress_message = f"📄 파일 파싱 중... ({completed_files}/{total_files})"

        self.logger.info(
            "📊 파싱 결과 - 성공 %d개, 실패 %d개, 총 청크 %d개",
            parsed_count,
            failed_count,
            total_chunk_count
        )

        if not all_texts:
            self.logger.warning("⚠️ 인덱싱할 텍스트 청크가 없습니다.")
            return

        # --- 2. 임베딩 & 업로드 (Embedding & Upload) ---
        # 이 단계에서만 GPU/CPU 로직을 분기합니다.

        self.logger.info("--- [2/2] 임베딩 및 업로드 시작 (모드: %s) ---", "GPU" if is_gpu_available else "CPU")
        
        # 진행률 업데이트 (임베딩 시작)
        if manager:
            manager.progress_message = f"🧠 임베딩 생성 중... (총 {len(all_texts)}개 청크)"

        if is_gpu_available:
            # GPU 모드: 수집된 모든 청크를 한 번에 처리 (빠름)
            if all_texts:
                self.logger.info("--- GPU 모드: 총 %d개 청크 일괄 처리 ---", len(all_texts))
                self._process_and_upload_batch(
                    repo,
                    embedder,
                    all_texts,
                    all_metas,
                    embedding_batch_size
                )
        else:
            # CPU 모드: OOM 방지를 위해 마이크로 배치로 잘라서 처리 (안정적)
            self.logger.warning("--- CPU 모드: %d개 청크를 %d개 단위로 분할 처리 ---",
                               len(all_texts), cpu_micro_batch_threshold)

            total_batches = (len(all_texts) + cpu_micro_batch_threshold - 1) // cpu_micro_batch_threshold
            
            for i in range(0, len(all_texts), cpu_micro_batch_threshold):
                batch_texts = all_texts[i:i + cpu_micro_batch_threshold]
                batch_metas = all_metas[i:i + cpu_micro_batch_threshold]

                if batch_texts:
                    batch_num = i // cpu_micro_batch_threshold + 1
                    self.logger.info(f"--- CPU 배치 {batch_num}/{total_batches} 처리 중... ---")
                    
                    # 진행률 업데이트
                    if manager:
                        manager.progress_message = f"🧠 임베딩 생성 중... (배치 {batch_num}/{total_batches})"
                    
                    self._process_and_upload_batch(
                        repo,
                        embedder,
                        batch_texts,
                        batch_metas,
                        embedding_batch_size
                    )

        # --- 3. SQLite 상태 업데이트 ---
        if file_hash_map:
            # file_hash_map의 값(해시)들을 리스트로 만듭니다.
            indexed_hashes = list(file_hash_map.values())
            if self.sqlite_meta.mark_files_indexed(indexed_hashes):
                self.logger.info("✅ %d개 파일 인덱싱 완료 표시", len(indexed_hashes))
            else:
                self.logger.warning("⚠️ 파일 인덱싱 표시 실패 (검색은 정상 작동)")

# -----------------------------------------------------------------------------
# BrowserHistoryCollector
# -----------------------------------------------------------------------------
class BrowserHistoryCollector:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.logger = logger.getChild(f"BrowserHistoryCollector[user={user_id}]")
        self.sqlite_meta = SQLiteMeta()
        self.browser_paths = self._get_browser_paths()
        self.browser_paths = self._get_browser_paths()
        self.parser = DocumentParser()

    def _extract_and_save_entities(self, text: str, source_id: str, source_type: str):
        """텍스트에서 엔티티를 추출하고 저장 (FileCollector와 로직 공유 가능)"""
        try:
            import re
            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', text)
            for email in emails:
                entity_id = self.sqlite_meta.upsert_entity(email, 'Person')
                if entity_id > 0:
                    self.sqlite_meta.add_entity_relation(entity_id, source_id, source_type, 'mentioned_in')

            tech_keywords = ['Python', 'Java', 'JavaScript', 'TypeScript', 'React', 'Vue', 'FastAPI', 
                           'Django', 'Spring', 'Docker', 'Kubernetes', 'AWS', 'Azure', 'GCP', 'SQL', 'NoSQL']
            for kw in tech_keywords:
                if kw.lower() in text.lower():
                    entity_id = self.sqlite_meta.upsert_entity(kw, 'Technology')
                    if entity_id > 0:
                        self.sqlite_meta.add_entity_relation(entity_id, source_id, source_type, 'mentioned_in')
        except Exception as e:
            self.logger.warning(f"엔티티 추출 중 오류: {e}")

    def _get_browser_paths(self) -> Dict[str, str]:
        """현재 운영체제에 맞는 브라우저 히스토리 DB 경로를 반환합니다."""
        system = platform.system()
        if system == 'Windows':
            return {
                'chrome': os.path.expanduser('~\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\History'),
                'edge': os.path.expanduser('~\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\History')
            }
        elif system == 'Darwin':  # macOS
            return {
                'chrome': os.path.expanduser('~/Library/Application Support/Google/Chrome/Default/History'),
                'edge': os.path.expanduser('~/Library/Application Support/Microsoft Edge/Default/History')
                # Firefox, Safari 등 다른 브라우저 지원 추가 가능
            }
        # TODO: Add Linux support
        return {}

    async def _crawl_and_extract_text(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        if not url.startswith(('http://', 'https://')): return None
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'lxml')
                    for s in soup(['script', 'style', 'nav', 'footer', 'aside']): s.decompose()
                    return soup.get_text(separator='\n', strip=True)
        except: return None

    def _batch_index_web_pages(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: 'BGEM3Embedder'):
        async def main():
            all_texts, all_metas = [], []
            connector = aiohttp.TCPConnector(limit=20)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [self._crawl_and_extract_text(session, item['url']) for item in history_data]
                crawled_contents = await asyncio.gather(*tasks)
                self.logger.info("📄 총 %d개 URL 중 %d개 크롤링 성공",
                                 len(history_data), len([c for c in crawled_contents if c]))
                for item, content in zip(history_data, crawled_contents):
                    if not content: continue
                    chunks = self.parser.chunk_text(content)
                    doc_id = f"web_{hashlib.md5(item['url'].encode()).hexdigest()}"
                    for i, chunk in enumerate(chunks):
                        all_texts.append(chunk)
                        all_metas.append({
                            'user_id': self.user_id,  # user_id 포함
                            'source': 'web', 
                            'url': item['url'], 
                            'title': item['title'], 
                            'doc_id': doc_id, 
                            'chunk_id': i, 
                            'timestamp': int(item['visit_time'].timestamp()), 
                            'snippet': chunk[:200],
                            'content': chunk
                        })
                        
                        # [New] 첫 번째 청크에서 엔티티 추출
                        if i == 0:
                            self._extract_and_save_entities(chunk[:2000], item['url'], 'web')
            if all_texts:
                self.logger.info("🧠 BGE-M3로 %d개 웹 청크 임베딩 생성...", len(all_texts))
                embeddings = embedder.encode_documents(all_texts, batch_size=64)
                dense_vectors, sparse_vectors = embeddings['dense_vecs'].tolist(), [embedder.convert_sparse_to_qdrant_format(lw) for lw in embeddings['lexical_weights']]
                if repo.qdrant.upsert_vectors(all_metas, dense_vectors, sparse_vectors):
                    self.logger.info("✅ Qdrant에 웹 청크 %d개 인덱싱 완료", len(dense_vectors))
                else:
                    self.logger.error("❌ Qdrant 웹 청크 인덱싱 실패")
        asyncio.run(main())

    def _get_browser_history(self, browser_name: str, incremental: bool) -> List[Dict[str, Any]]:
        db_path = self.browser_paths.get(browser_name.lower())
        if not db_path or not os.path.exists(db_path): return []
        temp_path, history_data = f"{db_path}_temp", []
        try:
            shutil.copy2(db_path, temp_path)
            conn, query, params = sqlite3.connect(temp_path), "SELECT url, title, last_visit_time FROM urls", ()
            if incremental and (last_time := self.sqlite_meta.get_last_browser_collection_time(self.user_id, browser_name)):
                webkit_ts = int((last_time - datetime(1601, 1, 1)).total_seconds() * 1_000_000)
                query, params = f"{query} WHERE last_visit_time > ?", (webkit_ts,)
            query += " ORDER BY last_visit_time DESC LIMIT 100"
            for row in conn.cursor().execute(query, params).fetchall():
                visit_time = datetime(1601, 1, 1) + timedelta(microseconds=row[2])
                if not self.sqlite_meta.is_browser_history_duplicate(self.user_id, row[0], visit_time):
                    history_data.append({'user_id': self.user_id, 'browser_name': browser_name, 'url': row[0], 'title': row[1], 'visit_time': visit_time})
            conn.close()
        except Exception as e:
            self.logger.error("%s 히스토리 수집 오류: %s", browser_name, e, exc_info=True)
        finally:
            if os.path.exists(temp_path): os.remove(temp_path)
        return history_data

    def collect_all_browser_history(self, incremental: bool = True) -> List[Dict[str, Any]]:
        return self._get_browser_history('Chrome', incremental) + self._get_browser_history('Edge', incremental)

    def save_browser_history_to_db(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: 'BGEM3Embedder') -> int:
        if not history_data or not repo: return 0
        saved_count = 0
        try:
            self.sqlite_meta.conn.execute("BEGIN TRANSACTION")
            for item in history_data:
                if self.sqlite_meta.insert_collected_browser_history(item): saved_count += 1
            self.sqlite_meta.conn.commit()
            self.logger.info("✅ SQLite 브라우저 히스토리 저장: %d개", saved_count)
        except Exception as e:
            self.sqlite_meta.conn.rollback()
            self.logger.error("❌ SQLite 히스토리 저장 실패: %s", e, exc_info=True)
            return 0
        self._batch_index_web_pages(history_data, repo, embedder)
        return saved_count

# -----------------------------------------------------------------------------
# ActiveApplicationCollector
# -----------------------------------------------------------------------------
class ActiveApplicationCollector:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.sqlite_meta = SQLiteMeta()
    def collect_active_applications(self) -> List[Dict[str, Any]]:
        active_apps = []
        for proc in psutil.process_iter(['name', 'exe', 'create_time']):
            try:
                if proc.info['exe'] and os.path.exists(proc.info['exe']):
                    active_apps.append({'user_id': self.user_id, 'app_name': proc.info['name'], 'app_path': proc.info['exe'], 'start_time': datetime.fromtimestamp(proc.info['create_time'])})
            except (psutil.NoSuchProcess, psutil.AccessDenied): continue
        return active_apps
    def save_active_apps_to_db(self, apps_data: List[Dict[str, Any]]) -> int:
        saved = 0
        for app in apps_data:
            if self.sqlite_meta.insert_collected_app(app): saved += 1
        return saved


# -----------------------------------------------------------------------------
# SessionProcessor
# -----------------------------------------------------------------------------
class SessionProcessor:
    """
    Raw logs (collected_apps, collected_browser_history)를 분석하여
    의미 있는 ActivitySession으로 그룹화하는 클래스
    """
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.sqlite_meta = SQLiteMeta()
        self.logger = logger.getChild(f"SessionProcessor[user={user_id}]")
        self.session_timeout = 300  # 5분 (초 단위)

    def process_sessions(self):
        """미처리 로그를 확인하여 세션을 생성하거나 기존 세션에 연결"""
        try:
            # 1. 미처리 앱 로그 가져오기
            unprocessed_apps = self.sqlite_meta.get_unprocessed_logs("collected_apps", self.user_id)
            # 2. 미처리 브라우저 로그 가져오기
            unprocessed_web = self.sqlite_meta.get_unprocessed_logs("collected_browser_history", self.user_id)
            
            all_logs = []
            for log in unprocessed_apps:
                all_logs.append({
                    'type': 'app', 'data': log, 'time': log['recorded_at'], 'id': log['id']
                })
            for log in unprocessed_web:
                all_logs.append({
                    'type': 'web', 'data': log, 'time': log['recorded_at'], 'id': log['id']
                })
            
            if not all_logs: return

            # 시간순 정렬
            all_logs.sort(key=lambda x: x['time'])

            current_session_id = None
            last_time = 0
            
            # 가장 최근 세션 확인 (이어붙이기 위해)
            # (간소화를 위해 여기서는 항상 새로운 세션 로직을 타거나, 
            #  메모리에 상태를 유지하지 않고 DB 기반으로 판단)
            
            # 간단한 로직:
            # 로그를 순회하며 이전 로그와 5분 이상 차이나면 새 세션 시작
            
            pending_updates = {'app': [], 'web': []}
            
            for i, log in enumerate(all_logs):
                log_time = log['time']
                
                if current_session_id is None:
                    # 첫 로그 -> 새 세션 생성
                    current_session_id = self._create_new_session(log)
                    last_time = log_time
                else:
                    # 시간 차이 확인
                    if log_time - last_time > self.session_timeout:
                        # 세션 종료 및 새 세션 시작
                        self._close_session(current_session_id, last_time)
                        current_session_id = self._create_new_session(log)
                    else:
                        # 기존 세션 유지 (업데이트는 나중에 한 번에 하거나 필요시)
                        pass
                
                last_time = log_time
                
                # 세션 ID 할당 대기열 추가
                if log['type'] == 'app':
                    pending_updates['app'].append(log['id'])
                else:
                    pending_updates['web'].append(log['id'])
                
                # 배치 업데이트 (또는 세션이 바뀌었을 때)
                if i == len(all_logs) - 1 or (i < len(all_logs)-1 and all_logs[i+1]['time'] - log_time > self.session_timeout):
                     self._flush_updates(current_session_id, pending_updates)
                     self._close_session(current_session_id, last_time) # 마지막 로그 시간으로 세션 종료 업데이트
                     pending_updates = {'app': [], 'web': []}
                     current_session_id = None # 리셋

        except Exception as e:
            self.logger.error(f"세션 처리 중 오류: {e}", exc_info=True)

    def _create_new_session(self, first_log) -> int:
        """새 세션 생성"""
        dominant_app = first_log['data'].get('app_name') if first_log['type'] == 'app' else "Browser"
        return self.sqlite_meta.create_activity_session(
            user_id=self.user_id,
            start_time=first_log['time'],
            dominant_app=dominant_app,
            summary="New Activity Started" # 나중에 AI로 업데이트
        )

    def _close_session(self, session_id, end_time):
        """세션 종료 시간 업데이트"""
        self.sqlite_meta.update_activity_session(session_id, end_time=end_time)

    def _flush_updates(self, session_id, updates):
        """로그에 세션 ID 매핑"""
        if updates['app']:
            self.sqlite_meta.link_logs_to_session("collected_apps", updates['app'], session_id)
        if updates['web']:
            self.sqlite_meta.link_logs_to_session("collected_browser_history", updates['web'], session_id)


# -----------------------------------------------------------------------------
# DataCollectionManager
# -----------------------------------------------------------------------------
class DataCollectionManager:
    def __init__(self, user_id: int, repository: Repository, embedder: 'BGEM3Embedder'):
        self.user_id = user_id
        self.logger = logger.getChild(f"DataCollectionManager[user={user_id}]")
        self.file_collector = FileCollector(user_id)
        self.browser_collector = BrowserHistoryCollector(user_id)
        self.app_collector = ActiveApplicationCollector(user_id)
        self.session_processor = SessionProcessor(user_id)  # Add SessionProcessor
        self.running, self.initial_collection_done = False, False
        self.progress, self.progress_message = 0.0, "초기화 중..."
        self.logger.info("RAG 시스템 핵심 컴포넌트 초기화 중...")
        try:
            self.repository = repository
            self.embedder = embedder
            self.document_parser = DocumentParser()
            self.logger.info("✅ RAG 시스템 컴포넌트 초기화 완료.")
        except Exception as e:
            self.logger.error("❌ RAG 시스템 컴포넌트 초기화 실패: %s", e, exc_info=True)
            self.repository = self.embedder = self.document_parser = None

    def start_collection(self, selected_folders: List[str]):
        if self.running:
            self.logger.debug("데이터 수집이 이미 실행 중입니다. 새로운 요청을 무시합니다.")
            return

        self.selected_folders = selected_folders
        self.running = True
        self.collection_thread = threading.Thread(target=self._collection_loop, daemon=True)
        self.collection_thread.start()
        folder_desc = "전체 사용자 폴더" if selected_folders is None else f"{len(selected_folders)}개 폴더"
        self.logger.info("사용자 %d의 데이터 수집을 시작했습니다. 대상: %s", self.user_id, folder_desc)
    
    def perform_initial_collection(self, selected_folders: List[str]):
        """초기 데이터 수집을 수행합니다."""
        if not self.repository:
            self.progress_message = "오류: RAG 시스템 초기화 실패"
            self.logger.error("Repository가 초기화되지 않아 초기 데이터 수집을 실행할 수 없습니다.")
            self.initial_collection_done = False
            return

        # 선택된 폴더를 나중에 백그라운드 수집에서도 사용할 수 있도록 저장합니다.
        self.selected_folders = selected_folders
        folder_desc = "전체 사용자 폴더" if selected_folders is None else f"{len(selected_folders)}개 폴더"
        self.logger.info("초기 데이터 수집을 시작합니다. 대상: %s", folder_desc)

        success = False
        progress_points = {
            "file_collection": 50.0,
            "browser_history": 65.0,
            "file_embedding": 85.0,
            "browser_embedding": 95.0,
            "complete": 100.0
        }
        try:
            self.progress_message = "📁 초기 파일 수집 중..."
            files = self.file_collector.collect_files_from_drive(
                False,
                self,
                self.selected_folders,
                (0.0, progress_points["file_collection"])
            )

            self.progress = max(self.progress, progress_points["file_collection"])
            self.progress_message = "🌐 브라우저 히스토리 수집 중..."
            history = self.browser_collector.collect_all_browser_history(False)
            self.logger.debug("브라우저 히스토리 %d개 항목 수집 완료", len(history))

            self.progress = max(self.progress, progress_points["browser_history"])
            self.progress_message = "💾 파일 임베딩 생성 및 저장 중..."
            self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser, manager=self)
            
            self.progress = max(self.progress, progress_points["file_embedding"])
            self.progress_message = "💾 웹 콘텐츠 임베딩 생성 및 저장 중..."
            self.browser_collector.save_browser_history_to_db(history, self.repository, self.embedder)

            self.progress = max(self.progress, progress_points["browser_embedding"])
            self.progress = progress_points["complete"]
            self.progress_message = "🎉 초기 데이터 수집 완료!"
            self.logger.info("초기 데이터 수집이 성공적으로 완료되었습니다.")
            success = True

        except Exception as e:
            self.logger.error("❌ 초기 데이터 수집 오류: %s", e, exc_info=True)
            self.progress_message = "오류 발생"
        finally:
            self.initial_collection_done = success
            if not success:
                self.logger.warning("초기 데이터 수집이 실패했습니다. 이후 요청 시 재시도할 수 있습니다.")
            else:
                # 초기 수집이 성공적으로 완료되면 백그라운드 수집 자동 시작
                self.logger.info("백그라운드 데이터 수집 스케줄러를 시작합니다.")
                self.start_collection(selected_folders)
    
    def stop_collection(self):
        self.running = False
        if hasattr(self, 'collection_thread') and self.collection_thread:
            self.collection_thread.join()
        self.logger.info("사용자 %d의 데이터 수집이 중지되었습니다.", self.user_id)
    
    def _collection_loop(self):
        intervals = {'file': 3600, 'browser': 1800, 'app': 300}
        last_run = {key: 0 for key in intervals}
        while self.running:
            if not self.repository: time.sleep(10); continue
            current_time = time.time()
            if current_time - last_run['file'] >= intervals['file']: self._collect_files(); last_run['file'] = current_time
            if current_time - last_run['browser'] >= intervals['browser']: self._collect_browser_history(); last_run['browser'] = current_time
            if current_time - last_run['app'] >= intervals['app']: self._collect_active_apps(); last_run['app'] = current_time
            
            # 세션 처리 (앱 수집 주기와 맞추거나 별도로 실행)
            self.session_processor.process_sessions()
            
            time.sleep(10)

    def _collect_files(self):
        files = self.file_collector.collect_files_from_drive(True, self, self.selected_folders)
        self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser, manager=self)
    
    def _collect_browser_history(self):
        history = self.browser_collector.collect_all_browser_history(True)
        self.browser_collector.save_browser_history_to_db(history, self.repository, self.embedder)
    
    def _collect_active_apps(self):
        apps = self.app_collector.collect_active_applications()
        self.app_collector.save_active_apps_to_db(apps)
    
# -----------------------------------------------------------------------------
# 전역 관리 함수
# -----------------------------------------------------------------------------
data_collection_managers = {}
def get_manager(user_id: int, repository: Repository, embedder: 'BGEM3Embedder') -> DataCollectionManager:
    if user_id not in data_collection_managers:
        data_collection_managers[user_id] = DataCollectionManager(
            user_id=user_id,
            repository=repository,
            embedder=embedder
        )
    return data_collection_managers[user_id]