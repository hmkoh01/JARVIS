#!/usr/bin/env python3
"""
Data Collector Module (Keyword-Centric Architecture)
- 파일, 브라우저 히스토리 데이터 수집
- KeyBERT 기반 키워드 추출 및 content_keywords 테이블 저장
- 간소화된 스키마에 맞게 최적화
"""
import os
import sys
import warnings
from pathlib import Path
import aiohttp
from bs4 import BeautifulSoup
import shutil
import time
import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import logging

# PDF 라이브러리 관련 경고 억제 (pypdfium2 메모리 정리 경고)
warnings.filterwarnings('ignore', message='.*Cannot close object.*library is destroyed.*')
# PyTorch CUDA 경고 억제 (Docling 모델 로딩 시)
warnings.filterwarnings('ignore', message='.*Attempting to deserialize object on.*CUDA.*')
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import hashlib

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.settings import settings
from .repository import Repository
from .sqlite import SQLite
from .document_parser import DocumentParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder

logger = logging.getLogger(__name__)

# KeywordExtractor 싱글톤 (Lazy Loading)
_keyword_extractor = None
_keyword_extractor_lock = threading.Lock()

def get_keyword_extractor():
    """KeywordExtractor 싱글톤 인스턴스를 반환합니다."""
    global _keyword_extractor
    if _keyword_extractor is None:
        with _keyword_extractor_lock:
            if _keyword_extractor is None:
                try:
                    from utils.keyword_extractor import KeywordExtractor
                    _keyword_extractor = KeywordExtractor.get_instance()
                    logger.info("✅ KeywordExtractor 싱글톤 초기화 완료")
                except Exception as e:
                    logger.warning(f"⚠️ KeywordExtractor 초기화 실패: {e}")
                    return None
    return _keyword_extractor


def init_worker_logging():
    """
    ProcessPoolExecutor 워커의 로깅을 완전히 억제하여
    불필요한 INFO 로그(모듈 초기화 등) 스팸을 방지합니다.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    root_logger.addHandler(console_handler)
    
    for logger_name in ['backend.config.logging_config', '__main__']:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def extract_keywords_from_text(text: str, top_n: int = 10) -> List[Tuple[str, float]]:
    """
    텍스트에서 키워드를 추출합니다.
    
    Args:
        text: 키워드를 추출할 텍스트
        top_n: 추출할 키워드 개수
    
    Returns:
        [(keyword, score), ...] 리스트
    """
    extractor = get_keyword_extractor()
    if extractor is None:
        return []
    
    try:
        return extractor.extract(text, top_n=top_n)
    except Exception as e:
        logger.debug(f"키워드 추출 오류: {e}")
        return []


def create_snippet(text: str, max_length: int = 200) -> str:
    """텍스트에서 스니펫을 생성합니다."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.7:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "..."


# -----------------------------------------------------------------------------
# FileCollector
# -----------------------------------------------------------------------------
class FileCollector:
    """사용자 드라이브의 파일들을 수집하는 클래스"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.logger = logger.getChild(f"FileCollector[user={user_id}]")
        self.sqlite = SQLite()
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

    def _generate_doc_id(self, file_path: str) -> str:
        """파일 경로 기반 doc_id 생성"""
        return f"file_{hashlib.md5(file_path.encode()).hexdigest()}"

    def is_file_modified(self, file_path: str, last_modified: datetime) -> bool:
        stored_modified = self.sqlite.get_file_last_modified(self.user_id, file_path)
        return stored_modified is None or last_modified > stored_modified

    def is_file_already_indexed(self, file_path: str) -> bool:
        """파일이 이미 인덱싱되었는지 확인"""
        doc_id = self._generate_doc_id(file_path)
        return self.sqlite.is_file_exists(self.user_id, doc_id)

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
        skipped_by_duplicate = 0
        
        progress_start, progress_end = progress_bounds
        progress_range = max(progress_end - progress_start, 0.0)

        if manager:
            manager.progress = progress_start

        total_paths = len(paths_to_scan)

        for i, folder_path in enumerate(paths_to_scan):
            normalized_path = os.path.normpath(folder_path)
            
            if manager and total_paths > 0: 
                manager.progress = progress_start + ((i + 1) / total_paths) * progress_range
                manager.progress_message = f"📁 스캔 중: {Path(normalized_path).name}"
            
            try:
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
                            
                            if incremental and not self.is_file_modified(file_path, modified_date):
                                continue
                            
                            # 이미 인덱싱된 파일은 스킵
                            if self.is_file_already_indexed(file_path):
                                skipped_by_duplicate += 1
                                continue
                            
                            collected_files.append({
                                'user_id': self.user_id,
                                'file_path': file_path,
                                'file_category': self.get_file_category(file_path),
                                'modified_date': modified_date,
                            })
                        except (PermissionError, OSError, FileNotFoundError): continue
            except Exception as e: 
                self.logger.error("폴더 스캔 오류 %s: %s", normalized_path, e, exc_info=True)
        
        if manager:
            if total_paths > 0:
                manager.progress = progress_end
            else:
                manager.progress = progress_start
        
        self.logger.info("📊 파일 수집 결과 - 총 스캔: %d, 확장자 제외: %d, 중복 제외: %d, 신규 파일: %d",
                         total_scanned, skipped_by_extension, skipped_by_duplicate, len(collected_files))
        
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
            conn = self.sqlite.get_user_connection(self.user_id)
            conn.execute("BEGIN TRANSACTION")
            for file_info in files:
                if self.sqlite.insert_collected_file(file_info):
                    saved_count += 1
                    if file_info['file_category'] in ['document', 'spreadsheet', 'presentation', 'code', 'note']:
                        text_files.append(file_info)
            conn.commit()
            self.logger.info("✅ SQLite 파일 메타데이터 저장: %d개, 텍스트 인덱싱 대상: %d개",
                             saved_count, len(text_files))
        except Exception as e: 
            conn = self.sqlite.get_user_connection(self.user_id)
            if conn:
                conn.rollback()
            self.logger.error("❌ SQLite 파일 저장 실패: %s", e, exc_info=True)
            return 0
        
        if text_files:
            self._batch_index_text_files(text_files, repo, embedder, parser, manager)
        else:
            self.logger.warning("⚠️ 텍스트 인덱싱 대상 파일이 없습니다.")
        return saved_count

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
                return None, file_info.get('file_name', Path(file_info['file_path']).name), f"Docling RuntimeError: {e}"
            except Exception as e:
                return None, file_info.get('file_name', Path(file_info['file_path']).name), f"Parsing Exception: {e}"
            if not chunk_infos:
                return None, file_info.get('file_name', Path(file_info['file_path']).name), "청크 없음"

            doc_id = f"file_{hashlib.md5(file_info['file_path'].encode()).hexdigest()}"

            texts = []
            metas = []
            full_text_for_keywords = []  # 키워드 추출용 전체 텍스트
            
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
                full_text_for_keywords.append(chunk['text'])

            file_name = Path(file_info['file_path']).name
            # 전체 텍스트를 결합하여 반환 (키워드 추출용)
            combined_text = '\n'.join(full_text_for_keywords)
            
            return (texts, metas, file_info['file_path'], len(chunk_infos), doc_id, combined_text), file_name, None
        except BaseException as e:
            return None, file_info.get('file_name', 'unknown'), f"Worker setup error: {e}"

    def _extract_and_save_file_keywords(
        self, 
        doc_id: str, 
        combined_text: str,
        file_path: str
    ):
        """파일에서 키워드를 추출하고 content_keywords 테이블에 저장합니다."""
        if not combined_text or len(combined_text.strip()) < 50:
            return
        
        try:
            # 키워드 추출 (top 10)
            keywords = extract_keywords_from_text(combined_text, top_n=10)
            
            if not keywords:
                self.logger.debug(f"키워드 추출 결과 없음: {file_path}")
                return
            
            # 스니펫 생성
            snippet = create_snippet(combined_text, max_length=200)
            
            # content_keywords 테이블에 저장할 데이터 준비
            keyword_entries = []
            for keyword, score in keywords:
                keyword_entries.append({
                    'user_id': self.user_id,
                    'source_type': 'file',
                    'source_id': doc_id,
                    'keyword': keyword,
                    'original_text': snippet
                })
            
            # 일괄 삽입
            if keyword_entries:
                inserted = self.sqlite.insert_content_keywords_batch(self.user_id, keyword_entries)
                if inserted > 0:
                    self.logger.debug(f"🔑 파일 키워드 저장: {Path(file_path).name} - {inserted}개")
                    
        except Exception as e:
            self.logger.warning(f"파일 키워드 추출/저장 오류 ({file_path}): {e}")

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
            self.logger.error("   ... ❌ 임베딩/업로드 중 치명적 오류: %s", e, exc_info=True)

    def _batch_index_text_files(
        self,
        text_files: List[Dict[str, Any]],
        repo: Repository,
        embedder: 'BGEM3Embedder',
        parser: DocumentParser,
        manager: Optional['DataCollectionManager'] = None
    ):
        # 중복 파일 경로 제거
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
        
        # 메모리 최적화: 설정에서 max_parallel_workers 로드 (기본값 2)
        max_parallel_workers = getattr(parser, 'max_parallel_workers', 2) if parser else 2
        
        self.logger.info(
            "📝 텍스트 파일 인덱싱 시작 - 파일 %d개, 사용 워커 %d개 (메모리 최적화)",
            len(text_files),
            max_parallel_workers
        )
        
        if manager:
            manager.progress_message = f"📄 파일 파싱 중... (총 {len(text_files)}개)"

        is_gpu_available = getattr(embedder, "device", "cpu") == "cuda"
        embedding_batch_size = 128 if is_gpu_available else 32
        cpu_micro_batch_threshold = 5000

        all_texts: List[str] = []
        all_metas: List[Dict[str, Any]] = []
        total_chunk_count = 0
        parsed_count = 0
        failed_count = 0
        
        # 키워드 추출용 데이터 수집
        files_for_keywords: List[Tuple[str, str, str]] = []  # (doc_id, combined_text, file_path)

        # 메모리 최적화: 워커 수 제한 (기존 min(cpu_count, 8) → max_parallel_workers)
        max_workers = max_parallel_workers
        parser_ref = parser.__class__ if parser is not None else DocumentParser

        self.logger.info("--- [1/3] 파일 파싱 시작 (워커 %d개, 메모리 최적화) ---", max_workers)
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
                    texts, metas, file_path, chunk_count, doc_id, combined_text = result

                    all_texts.extend(texts)
                    all_metas.extend(metas)

                    total_chunk_count += len(texts)
                    parsed_count += 1
                    
                    # 키워드 추출용 데이터 저장
                    if combined_text:
                        files_for_keywords.append((doc_id, combined_text, file_path))
                    
                    self.logger.info("   ✓ %s: %d개 청크 (파싱 완료)", file_name, chunk_count)
                else:
                    failed_count += 1
                    self.logger.warning("   ✗ 파일 파싱 오류 %s: %s", file_name, error)
                
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

        # --- [2/3] 키워드 추출 및 저장 ---
        self.logger.info("--- [2/3] 키워드 추출 및 저장 시작 ---")
        if manager:
            manager.progress_message = f"🔑 키워드 추출 중... (총 {len(files_for_keywords)}개 파일)"
        
        keyword_count = 0
        for doc_id, combined_text, file_path in files_for_keywords:
            self._extract_and_save_file_keywords(doc_id, combined_text, file_path)
            keyword_count += 1
            if manager and keyword_count % 10 == 0:
                manager.progress_message = f"🔑 키워드 추출 중... ({keyword_count}/{len(files_for_keywords)})"
        
        self.logger.info("✅ 파일 키워드 추출 완료: %d개 파일", keyword_count)

        # --- [3/3] 임베딩 및 업로드 ---
        self.logger.info("--- [3/3] 임베딩 및 업로드 시작 (모드: %s) ---", "GPU" if is_gpu_available else "CPU")
        
        if manager:
            manager.progress_message = f"🧠 임베딩 생성 중... (총 {len(all_texts)}개 청크)"

        if is_gpu_available:
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
            self.logger.warning("--- CPU 모드: %d개 청크를 %d개 단위로 분할 처리 ---",
                               len(all_texts), cpu_micro_batch_threshold)

            total_batches = (len(all_texts) + cpu_micro_batch_threshold - 1) // cpu_micro_batch_threshold
            
            for i in range(0, len(all_texts), cpu_micro_batch_threshold):
                batch_texts = all_texts[i:i + cpu_micro_batch_threshold]
                batch_metas = all_metas[i:i + cpu_micro_batch_threshold]

                if batch_texts:
                    batch_num = i // cpu_micro_batch_threshold + 1
                    self.logger.info(f"--- CPU 배치 {batch_num}/{total_batches} 처리 중... ---")
                    
                    if manager:
                        manager.progress_message = f"🧠 임베딩 생성 중... (배치 {batch_num}/{total_batches})"
                    
                    self._process_and_upload_batch(
                        repo,
                        embedder,
                        batch_texts,
                        batch_metas,
                        embedding_batch_size
                    )

        self.logger.info("✅ 파일 인덱싱 완료: %d개 파일", parsed_count)


# -----------------------------------------------------------------------------
# BrowserHistoryCollector
# -----------------------------------------------------------------------------

# 동시 요청 제한 (Rate Limiting) - DoS 오해 및 IP 차단 방지
MAX_CONCURRENT_REQUESTS = 10
REQUEST_DELAY_SECONDS = 0.1  # 요청 간 최소 딜레이


class BrowserHistoryCollector:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.logger = logger.getChild(f"BrowserHistoryCollector[user={user_id}]")
        self.sqlite = SQLite()
        self.browser_paths = self._get_browser_paths()
        self.parser = DocumentParser()
        # 세마포어: 동시에 MAX_CONCURRENT_REQUESTS개까지만 요청 허용
        self._semaphore: Optional[asyncio.Semaphore] = None

    def _get_browser_paths(self) -> Dict[str, str]:
        """현재 운영체제에 맞는 브라우저 히스토리 DB 경로를 반환합니다."""
        import platform
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
            }
        return {}

    def _fetch_web_content(self, url: str) -> Optional[str]:
        """URL에서 메인 콘텐츠 텍스트를 추출합니다."""
        try:
            from utils.web_crawler import fetch_web_content
            return fetch_web_content(url, timeout=3)
        except ImportError:
            self.logger.warning("web_crawler 모듈을 찾을 수 없습니다.")
            return None
        except Exception as e:
            self.logger.debug(f"웹 콘텐츠 추출 실패 ({url}): {e}")
            return None

    def _extract_and_save_web_keywords(
        self,
        log_id: int,
        url: str,
        title: str,
        content: str
    ):
        """웹 페이지에서 키워드를 추출하고 저장합니다."""
        if not content or len(content.strip()) < 50:
            return
        
        try:
            # 키워드 추출 (top 10)
            keywords = extract_keywords_from_text(content, top_n=10)
            
            if not keywords:
                return
            
            # 스니펫 생성
            snippet = create_snippet(content, max_length=200)
            
            # content_keywords 테이블에 저장할 데이터 준비
            keyword_entries = []
            for keyword, score in keywords:
                keyword_entries.append({
                    'user_id': self.user_id,
                    'source_type': 'web',
                    'source_id': str(log_id),
                    'keyword': keyword,
                    'original_text': snippet
                })
            
            # 일괄 삽입
            if keyword_entries:
                inserted = self.sqlite.insert_content_keywords_batch(self.user_id, keyword_entries)
                if inserted > 0:
                    self.logger.debug(f"🔑 웹 키워드 저장: {title[:30]}... - {inserted}개")
                    
        except Exception as e:
            self.logger.warning(f"웹 키워드 추출/저장 오류 ({url}): {e}")

    async def _crawl_with_rate_limit(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        세마포어를 사용하여 동시 요청 수를 제한하면서 URL을 크롤링합니다.
        DoS 공격으로 오해받거나 Rate Limit에 걸리는 것을 방지합니다.
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        async with self._semaphore:
            # 요청 간 최소 딜레이 (서버 부하 방지)
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            return await self._crawl_and_extract_text(session, url)
    
    async def _crawl_and_extract_text(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        비동기로 URL에서 텍스트를 추출합니다.
        
        SPA 사이트 및 동적 콘텐츠는 내용이 비어있거나 짧을 수 있으므로
        100자 미만의 콘텐츠는 건너뜁니다.
        """
        if not url.startswith(('http://', 'https://')): 
            return None
        
        url_lower = url.lower()
        
        # 스킵할 URL 패턴 (SPA, 소셜미디어, 인증 페이지 등)
        skip_patterns = [
            # 소셜미디어 (대부분 SPA)
            'youtube.com', 'youtu.be', 'facebook.com', 'instagram.com', 
            'twitter.com', 'x.com', 'tiktok.com', 'linkedin.com/feed',
            'reddit.com', 'discord.com', 'slack.com', 'telegram.org',
            # 검색 엔진
            'google.com/search', 'bing.com/search', 'naver.com/search',
            'duckduckgo.com', 'yahoo.com/search',
            # 인증/로그인 페이지
            'login', 'signin', 'signup', 'auth', 'oauth', 'sso',
            # 파일 다운로드/스트리밍
            '.pdf', '.doc', '.zip', '.mp4', '.mp3', '.avi',
            'drive.google.com', 'dropbox.com', 'onedrive.live',
            # 이메일
            'mail.google.com', 'outlook.live', 'mail.naver',
            # 기타 SPA 앱
            'notion.so', 'figma.com', 'canva.com', 'trello.com',
            'github.com/settings', 'gitlab.com/-/profile',
        ]
        if any(pattern in url_lower for pattern in skip_patterns):
            return None
        
        # 파일 확장자 체크 (HTML이 아닌 리소스 스킵)
        skip_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', 
                          '.css', '.js', '.json', '.xml', '.rss', '.ico']
        if any(url_lower.endswith(ext) for ext in skip_extensions):
            return None
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5), headers=headers) as response:
                if response.status != 200:
                    return None
                
                # Content-Type 확인 (HTML만 처리)
                content_type = response.headers.get('Content-Type', '')
                if 'text/html' not in content_type and 'application/xhtml' not in content_type:
                    return None
                
                html = await response.text()
                
                # HTML이 너무 짧으면 SPA일 가능성 높음
                if len(html) < 500:
                    return None
                
                # trafilatura 사용 시도 (더 정확한 본문 추출)
                try:
                    import trafilatura
                    extracted = trafilatura.extract(
                        html, 
                        include_comments=False, 
                        include_tables=False,
                        include_links=False,
                        include_images=False,
                        favor_recall=False  # 정확도 우선
                    )
                    if extracted and len(extracted.strip()) >= 100:  # 100자 이상만 유효
                        return extracted
                except ImportError:
                    pass
                
                # BeautifulSoup 폴백
                soup = BeautifulSoup(html, 'lxml')
                
                # 불필요한 태그 제거
                for tag in soup(['script', 'style', 'nav', 'footer', 'aside', 
                                'header', 'noscript', 'iframe', 'form', 'button']): 
                    tag.decompose()
                
                # 본문 추출 시도 (article, main, content 등 우선)
                main_content = None
                for selector in ['article', 'main', '[role="main"]', '.content', '#content', '.post', '.article']:
                    main_content = soup.select_one(selector)
                    if main_content:
                        break
                
                if main_content:
                    text = main_content.get_text(separator='\n', strip=True)
                else:
                    text = soup.get_text(separator='\n', strip=True)
                
                # 최소 100자 이상인 경우만 반환 (SPA 쓰레기 데이터 방지)
                if len(text.strip()) >= 100:
                    return text
                    
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
        return None

    def _batch_index_web_pages(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: 'BGEM3Embedder'):
        """
        웹 페이지를 일괄로 크롤링하고 인덱싱합니다.
        
        세마포어를 사용하여 동시 요청 수를 제한합니다 (Rate Limiting).
        """
        async def main():
            all_texts, all_metas = [], []
            crawled_items = []  # 키워드 추출용
            
            # 세마포어 초기화
            self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
            
            # TCP 연결 수도 제한
            connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS)
            async with aiohttp.ClientSession(connector=connector) as session:
                # 세마포어로 동시 요청 수 제한
                tasks = [self._crawl_with_rate_limit(session, item['url']) for item in history_data]
                crawled_contents = await asyncio.gather(*tasks, return_exceptions=True)
                
                # 예외 처리: 실패한 요청은 None으로 대체
                crawled_contents = [
                    None if isinstance(c, Exception) else c 
                    for c in crawled_contents
                ]
                
                success_count = len([c for c in crawled_contents if c])
                self.logger.info("📄 총 %d개 URL 중 %d개 크롤링 성공", len(history_data), success_count)
                
                for item, content in zip(history_data, crawled_contents):
                    if not content: 
                        continue
                    
                    # 키워드 추출용 데이터 저장
                    crawled_items.append({
                        'item': item,
                        'content': content
                    })
                    
                    chunks = self.parser.chunk_text(content)
                    doc_id = f"web_{hashlib.md5(item['url'].encode()).hexdigest()}"
                    for i, chunk in enumerate(chunks):
                        all_texts.append(chunk)
                        all_metas.append({
                            'user_id': self.user_id,
                            'source': 'web', 
                            'url': item['url'], 
                            'title': item['title'], 
                            'doc_id': doc_id, 
                            'chunk_id': i, 
                            'timestamp': int(item['visit_time'].timestamp()), 
                            'snippet': chunk[:200],
                            'content': chunk
                        })
            
            # 키워드 추출 및 저장
            if crawled_items:
                self.logger.info("🔑 웹 페이지 키워드 추출 중... (%d개)", len(crawled_items))
                for crawled in crawled_items:
                    item = crawled['item']
                    content = crawled['content']
                    log_id = item.get('log_id', 0)
                    if log_id:
                        self._extract_and_save_web_keywords(
                            log_id=log_id,
                            url=item['url'],
                            title=item.get('title', ''),
                            content=content
                        )
                self.logger.info("✅ 웹 키워드 추출 완료")
            
            # Qdrant 인덱싱
            if all_texts:
                self.logger.info("🧠 BGE-M3로 %d개 웹 청크 임베딩 생성...", len(all_texts))
                embeddings = embedder.encode_documents(all_texts, batch_size=64)
                dense_vectors = embeddings['dense_vecs'].tolist()
                sparse_vectors = [embedder.convert_sparse_to_qdrant_format(lw) for lw in embeddings['lexical_weights']]
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
            if incremental and (last_time := self.sqlite.get_last_browser_collection_time(self.user_id, browser_name)):
                webkit_ts = int((last_time - datetime(1601, 1, 1)).total_seconds() * 1_000_000)
                query, params = f"{query} WHERE last_visit_time > ?", (webkit_ts,)
            query += " ORDER BY last_visit_time DESC LIMIT 100"
            for row in conn.cursor().execute(query, params).fetchall():
                visit_time = datetime(1601, 1, 1) + timedelta(microseconds=row[2])
                if not self.sqlite.is_browser_log_duplicate(self.user_id, row[0], visit_time):
                    history_data.append({
                        'user_id': self.user_id, 
                        'browser_name': browser_name, 
                        'url': row[0], 
                        'title': row[1], 
                        'visit_time': visit_time
                    })
            conn.close()
        except Exception as e:
            self.logger.error("%s 히스토리 수집 오류: %s", browser_name, e, exc_info=True)
        finally:
            if os.path.exists(temp_path): os.remove(temp_path)
        return history_data

    def collect_all_browser_history(self, incremental: bool = True) -> List[Dict[str, Any]]:
        return self._get_browser_history('Chrome', incremental) + self._get_browser_history('Edge', incremental)

    def save_browser_history_to_db(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: 'BGEM3Embedder') -> int:
        if not history_data or not repo: 
            return 0
        
        saved_count = 0
        saved_items = []  # 저장된 항목 (log_id 포함)
        
        try:
            conn = self.sqlite.get_user_connection(self.user_id)
            conn.execute("BEGIN TRANSACTION")
            for item in history_data:
                log_id = self.sqlite.insert_collected_browser_history(item)
                if log_id:
                    saved_count += 1
                    # log_id를 item에 추가
                    item['log_id'] = log_id
                    saved_items.append(item)
            conn.commit()
            self.logger.info("✅ SQLite 브라우저 히스토리 저장: %d개", saved_count)
        except Exception as e:
            conn = self.sqlite.get_user_connection(self.user_id)
            if conn:
                conn.rollback()
            self.logger.error("❌ SQLite 히스토리 저장 실패: %s", e, exc_info=True)
            return 0
        
        # 저장된 항목만 인덱싱 (log_id 포함)
        if saved_items:
            self._batch_index_web_pages(saved_items, repo, embedder)
        
        return saved_count


# -----------------------------------------------------------------------------
# DataCollectionManager
# -----------------------------------------------------------------------------
class DataCollectionManager:
    """데이터 수집 관리자 (키워드 추출 포함)"""
    def __init__(self, user_id: int, repository: Repository, embedder: 'BGEM3Embedder'):
        self.user_id = user_id
        self.logger = logger.getChild(f"DataCollectionManager[user={user_id}]")
        self.file_collector = FileCollector(user_id)
        self.browser_collector = BrowserHistoryCollector(user_id)
        self.running, self.initial_collection_done = False, False
        self.progress, self.progress_message = 0.0, "초기화 중..."
        self.logger.info("RAG 시스템 핵심 컴포넌트 초기화 중...")
        try:
            self.repository = repository
            self.embedder = embedder
            self.document_parser = DocumentParser()
            
            # KeywordExtractor 사전 초기화 (Lazy Loading이지만 미리 준비)
            get_keyword_extractor()
            
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
            self.progress_message = "💾 파일 임베딩 및 키워드 추출 중..."
            self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser, manager=self)
            
            self.progress = max(self.progress, progress_points["file_embedding"])
            self.progress_message = "💾 웹 콘텐츠 임베딩 및 키워드 추출 중..."
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
                # 진행률 100% 유지 (백그라운드 스케줄러 시작 시 덮어쓰지 않도록)
                self.progress = 100.0
                self.progress_message = "✅ 수집 완료 - 백그라운드 동기화 중"
                
                # 초기 데이터 수집 완료 후 추천 분석 즉시 트리거
                try:
                    from main import trigger_recommendation_analysis
                    asyncio.create_task(trigger_recommendation_analysis(force_recommend=True))
                    self.logger.info("🎯 초기 추천 분석이 트리거되었습니다.")
                except Exception as e:
                    self.logger.warning(f"추천 분석 트리거 실패 (무시됨): {e}")
                
                self.logger.info("백그라운드 데이터 수집 스케줄러를 시작합니다.")
                self.start_collection(selected_folders)
    
    def stop_collection(self):
        self.running = False
        if hasattr(self, 'collection_thread') and self.collection_thread:
            self.collection_thread.join()
        self.logger.info("사용자 %d의 데이터 수집이 중지되었습니다.", self.user_id)
    
    def _collection_loop(self):
        """백그라운드 수집 루프 (파일 및 브라우저만)"""
        intervals = {'file': 3600, 'browser': 1800}
        last_run = {key: 0 for key in intervals}
        while self.running:
            if not self.repository: 
                time.sleep(10)
                continue
            current_time = time.time()
            
            # 백그라운드 동기화 중에는 진행률을 100%로 유지
            if self.initial_collection_done:
                self.progress = 100.0
                self.progress_message = "✅ 수집 완료 - 백그라운드 동기화 중"
            
            if current_time - last_run['file'] >= intervals['file']: 
                self._collect_files()
                last_run['file'] = current_time
            if current_time - last_run['browser'] >= intervals['browser']: 
                self._collect_browser_history()
                last_run['browser'] = current_time
            time.sleep(10)

    def _collect_files(self):
        files = self.file_collector.collect_files_from_drive(True, self, self.selected_folders)
        self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser, manager=self)
    
    def _collect_browser_history(self):
        history = self.browser_collector.collect_all_browser_history(True)
        self.browser_collector.save_browser_history_to_db(history, self.repository, self.embedder)

    
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
