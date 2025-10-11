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
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import asyncio
import hashlib
from PIL import ImageGrab

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config.settings import settings
from .repository import Repository
from .sqlite_meta import SQLiteMeta
from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder
from .document_parser import DocumentParser

# -----------------------------------------------------------------------------
# FileCollector
# -----------------------------------------------------------------------------
class FileCollector:
    """사용자 드라이브의 파일들을 수집하는 클래스"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.sqlite_meta = SQLiteMeta()
        self.supported_extensions = {
            'document': ['.txt', '.doc', '.docx', '.pdf', '.hwp', '.md', '.rtf', '.odt', '.tex'],
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

    def get_user_folders(self) -> List[Dict[str, Any]]:
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
                            
                            dir_size = self._get_directory_size(entry.path)
                            
                            folders.append({
                                'name': entry.name,
                                'path': entry.path,
                                'size_formatted': self._format_size(dir_size),
                                'modified_date': datetime.fromtimestamp(stat.st_mtime)
                            })
                        except (OSError, PermissionError):
                            continue
        except Exception as e:
            print(f"사용자 폴더를 읽는 중 오류 발생: {e}")

        return sorted(folders, key=lambda x: x['name'].lower())

    def collect_files_from_drive(self, incremental: bool, manager: 'DataCollectionManager', selected_folders: Optional[List[str]]) -> List[Dict[str, Any]]:
        paths_to_scan = []
        if selected_folders is None:
            # "전체 사용자 폴더 스캔"이 선택된 경우, 기본 폴더 목록을 가져옵니다.
            default_folders = self.get_user_folders()
            paths_to_scan = [folder['path'] for folder in default_folders]
        else:
            paths_to_scan = selected_folders
        
        if not paths_to_scan: 
            print("⚠️ 스캔할 폴더가 없습니다.")
            return []

        collected_files = []
        total_scanned = 0
        skipped_by_extension = 0
        skipped_by_hash = 0
        
        for i, folder_path in enumerate(paths_to_scan):
            # 경로를 운영체제에 맞게 정규화하여 경로 구분자 문제를 해결합니다.
            normalized_path = os.path.normpath(folder_path)
            
            if manager: 
                # 0-80% 범위에서 진행률 계산
                manager.progress = (i / len(paths_to_scan)) * 80.0
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
                print(f"폴더 스캔 오류 {normalized_path}: {e}")
        
        # 파일 수집 완료 시 80%로 설정
        if manager:
            manager.progress = 80.0
        
        # 수집 결과 로깅
        print(f"\n📊 파일 수집 결과:")
        print(f"   - 총 스캔한 파일: {total_scanned}개")
        print(f"   - 지원되지 않는 확장자로 제외: {skipped_by_extension}개")
        print(f"   - 이미 수집된 파일(중복)로 제외: {skipped_by_hash}개")
        print(f"   - 새로 수집할 파일: {len(collected_files)}개")
        
        if len(collected_files) == 0 and total_scanned > 0:
            print(f"\n⚠️ 지원되는 확장자 목록: {', '.join(sorted(self.allowed_extensions))}")
        
        return collected_files

    def save_files_to_db(self, files: List[Dict[str, Any]], repo: Repository, embedder: BGEM3Embedder, parser: DocumentParser) -> int:
        if not files:
            print("⚠️ 저장할 파일이 없습니다.")
            return 0
        if not repo:
            print("⚠️ Repository가 초기화되지 않았습니다.")
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
            print(f"✅ SQLite 파일 메타데이터 저장: {saved_count}개")
            print(f"   - 텍스트 인덱싱 대상 파일: {len(text_files)}개")
        except Exception as e: 
            self.sqlite_meta.conn.rollback()
            print(f"❌ SQLite 파일 저장 실패: {e}")
            return 0
        
        if text_files:
            self._batch_index_text_files(text_files, repo, embedder, parser)
        else:
            print("⚠️ 텍스트 인덱싱 대상 파일이 없습니다.")
        return saved_count

    def _batch_index_text_files(self, text_files: List[Dict[str, Any]], repo: Repository, embedder: BGEM3Embedder, parser: DocumentParser):
        print(f"\n📝 텍스트 파일 인덱싱 시작... ({len(text_files)}개 파일)")
        
        all_texts, all_metas = [], []
        parsed_count = 0
        failed_count = 0
        file_hash_map = {}  # file_hash를 추적하기 위한 맵
        
        for file_info in text_files:
            try:
                chunk_infos = parser.parse_and_chunk(file_info['file_path'])
                if not chunk_infos:
                    print(f"⚠️ 청크 없음: {file_info['file_name']}")
                    failed_count += 1
                    continue
                
                parsed_count += 1
                doc_id = f"file_{hashlib.md5(file_info['file_path'].encode()).hexdigest()}"
                file_hash = file_info.get('file_hash', '')
                
                for chunk in chunk_infos:
                    all_texts.append(chunk['text'])
                    all_metas.append({'source': 'file', 'path': file_info['file_path'], 'doc_id': doc_id, 'chunk_id': chunk['chunk_id'], 'snippet': chunk['snippet']})
                    
                # 파일 해시 매핑 저장
                if file_hash:
                    file_hash_map[file_info['file_path']] = file_hash
                
                print(f"   ✓ {file_info['file_name']}: {len(chunk_infos)}개 청크")
            except Exception as e:
                failed_count += 1
                print(f"   ✗ 파일 파싱 오류 {file_info['file_name']}: {e}")
        
        print(f"\n📊 파싱 결과: 성공 {parsed_count}개, 실패 {failed_count}개, 총 청크 {len(all_texts)}개")
        
        if not all_texts:
            print("⚠️ 인덱싱할 텍스트 청크가 없습니다.")
            return
            
        print(f"🧠 BGE-M3로 {len(all_texts)}개 파일 청크 임베딩 생성...")
        embeddings = embedder.encode_documents(all_texts, batch_size=12)
        dense_vectors, sparse_vectors = embeddings['dense_vecs'].tolist(), [embedder.convert_sparse_to_qdrant_format(lw) for lw in embeddings['lexical_weights']]
        
        if repo.qdrant.upsert_vectors(all_metas, dense_vectors, sparse_vectors):
            print(f"✅ Qdrant에 파일 청크 {len(dense_vectors)}개 인덱싱 완료")
            
            # Qdrant 저장 성공 시 SQLite에 인덱싱 완료 표시
            if file_hash_map:
                indexed_hashes = list(file_hash_map.values())
                if self.sqlite_meta.mark_files_indexed(indexed_hashes):
                    print(f"✅ {len(indexed_hashes)}개 파일 인덱싱 완료 표시")
                else:
                    print(f"⚠️ 파일 인덱싱 표시 실패 (검색은 정상 작동)")
        else:
            print("❌ Qdrant 파일 인덱싱 실패")

# -----------------------------------------------------------------------------
# BrowserHistoryCollector
# -----------------------------------------------------------------------------
class BrowserHistoryCollector:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.sqlite_meta = SQLiteMeta()
        self.browser_paths = {'chrome': os.path.expanduser('~\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\History'), 'edge': os.path.expanduser('~\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\History')}
        self.parser = DocumentParser()

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

    def _batch_index_web_pages(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: BGEM3Embedder):
        async def main():
            all_texts, all_metas = [], []
            async with aiohttp.ClientSession() as session:
                tasks = [self._crawl_and_extract_text(session, item['url']) for item in history_data]
                crawled_contents = await asyncio.gather(*tasks)
                print(f"📄 총 {len(history_data)}개 URL 중 {len([c for c in crawled_contents if c])}개 크롤링 성공")
                for item, content in zip(history_data, crawled_contents):
                    if not content: continue
                    chunks = self.parser.chunk_text(content)
                    doc_id = f"web_{hashlib.md5(item['url'].encode()).hexdigest()}"
                    for i, chunk in enumerate(chunks):
                        all_texts.append(chunk)
                        all_metas.append({'source': 'web', 'url': item['url'], 'title': item['title'], 'doc_id': doc_id, 'chunk_id': i, 'timestamp': int(item['visit_time'].timestamp()), 'snippet': chunk[:200]})
            if all_texts:
                print(f"🧠 BGE-M3로 {len(all_texts)}개 웹 청크 임베딩 생성...")
                embeddings = embedder.encode_documents(all_texts, batch_size=12)
                dense_vectors, sparse_vectors = embeddings['dense_vecs'].tolist(), [embedder.convert_sparse_to_qdrant_format(lw) for lw in embeddings['lexical_weights']]
                if repo.qdrant.upsert_vectors(all_metas, dense_vectors, sparse_vectors):
                    print(f"✅ Qdrant에 웹 청크 {len(dense_vectors)}개 인덱싱 완료")
                else: print("❌ Qdrant 웹 청크 인덱싱 실패")
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
        except Exception as e: print(f"{browser_name} 히스토리 수집 오류: {e}")
        finally:
            if os.path.exists(temp_path): os.remove(temp_path)
        return history_data

    def collect_all_browser_history(self, incremental: bool = True) -> List[Dict[str, Any]]:
        return self._get_browser_history('Chrome', incremental) + self._get_browser_history('Edge', incremental)

    def save_browser_history_to_db(self, history_data: List[Dict[str, Any]], repo: Repository, embedder: BGEM3Embedder) -> int:
        if not history_data or not repo: return 0
        saved_count = 0
        try:
            self.sqlite_meta.conn.execute("BEGIN TRANSACTION")
            for item in history_data:
                if self.sqlite_meta.insert_collected_browser_history(item): saved_count += 1
            self.sqlite_meta.conn.commit()
            print(f"✅ SQLite 브라우저 히스토리 저장: {saved_count}개")
        except Exception as e: self.sqlite_meta.conn.rollback(); print(f"❌ SQLite 히스토리 저장 실패: {e}"); return 0
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
# ScreenActivityCollector
# -----------------------------------------------------------------------------
class ScreenActivityCollector:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.sqlite_meta = SQLiteMeta()
        self.screenshot_dir = Path("uploads/screenshots")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.llm = self._initialize_llm()
    
    def _initialize_llm(self):
        try:
            if settings.GEMINI_API_KEY:
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY); return genai.GenerativeModel(settings.GEMINI_MODEL)
        except Exception as e: print(f"LLM 초기화 오류: {e}")
        return None
    
    def capture_screenshot(self) -> Optional[Tuple[bytes, str]]:
        try:
            screenshot = ImageGrab.grab()
            filename = f"screenshot_{self.user_id}_{datetime.now():%Y%m%d_%H%M%S}.png"
            file_path = self.screenshot_dir / filename
            screenshot.save(file_path, 'PNG')
            with open(file_path, 'rb') as f: return f.read(), str(file_path)
        except Exception as e: print(f"스크린샷 캡처 오류: {e}"); return None
    
    async def analyze_screenshot_with_llm(self, image_data: bytes) -> Dict[str, Any]:
        if not self.llm: return self._fallback_analysis()
        try:
            import base64, re
            prompt = f'JSON 응답만 제공해주세요: ... data:image/png;base64,{base64.b64encode(image_data).decode("utf-8")}' # 프롬프트 생략
            response = await self.llm.generate_content_async(prompt)
            if (match := re.search(r'\{.*\}', response.text, re.DOTALL)): return json.loads(match.group())
        except Exception as e: print(f"LLM 분석 오류: {e}")
        return self._fallback_analysis()
    
    def _fallback_analysis(self) -> Dict[str, Any]:
        return {"activity_description": "스크린샷 캡처됨", "activity_category": "unknown", "detected_text": []}
    
    def save_screen_activity_to_db(self, screenshot_data: bytes, file_path: str, analysis: Dict[str, Any], repo: Repository, embedder: BGEM3Embedder) -> bool:
        try:
            screen = ImageGrab.grab()
            success = self.sqlite_meta.insert_collected_screenshot({
                'user_id': self.user_id, 'screenshot_path': file_path,
                'activity_description': analysis.get('activity_description', ''),
                'activity_category': analysis.get('activity_category', 'unknown'),
                'detected_text': json.dumps(analysis.get('detected_text', [])),
                'screen_resolution': f"{screen.width}x{screen.height}"
            })
            if success: self._index_screen_activity_for_rag(file_path, analysis, repo, embedder)
            return success
        except Exception as e:
            print(f"화면 활동 저장 오류: {e}")
            return False

    def _index_screen_activity_for_rag(self, file_path: str, analysis: Dict[str, Any], repo: Repository, embedder: BGEM3Embedder):
        content = f"화면 활동 요약: {analysis.get('activity_description', '')}\n화면에서 감지된 텍스트: {' '.join(analysis.get('detected_text', []))}"
        if not content.strip() or not repo: return
        try:       
            embeddings = embedder.encode_documents([content], batch_size=1)
            dense_vec, sparse_vec_data = embeddings['dense_vecs'][0].tolist(), embedder.convert_sparse_to_qdrant_format(embeddings['lexical_weights'][0])
            
            # doc_id 생성 (파일 경로 기반)
            doc_id = f"screen_{hashlib.md5(file_path.encode()).hexdigest()}"
            
            meta = {
                'source': 'screen',
                'doc_id': doc_id,
                'path': file_path,
                'description': analysis.get('activity_description', ''),
                'category': analysis.get('activity_category', 'unknown'),
                'timestamp': int(datetime.utcnow().timestamp()),
                'content': content,  # 전체 내용
                'snippet': content[:500]  # 검색 결과용 스니펫 (500자 제한)
            }
            if not repo.qdrant.upsert_vectors([meta], [dense_vec], [sparse_vec_data]):
                print("❌ Qdrant 화면 활동 인덱싱 실패")
        except Exception as e:
            print(f"화면 활동 RAG 인덱싱 오류: {e}")

# -----------------------------------------------------------------------------
# DataCollectionManager
# -----------------------------------------------------------------------------
class DataCollectionManager:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.file_collector = FileCollector(user_id)
        self.browser_collector = BrowserHistoryCollector(user_id)
        self.app_collector = ActiveApplicationCollector(user_id)
        self.screen_collector = ScreenActivityCollector(user_id)
        self.running, self.initial_collection_done = False, False
        self.progress, self.progress_message = 0.0, "초기화 중..."
        print("RAG 시스템 핵심 컴포넌트 초기화 중...")
        try:
            self.repository = Repository()
            self.embedder = BGEM3Embedder()
            self.document_parser = DocumentParser()
            print("✅ RAG 시스템 컴포넌트 초기화 완료.")
        except Exception as e:
            print(f"❌ RAG 시스템 컴포넌트 초기화 실패: {e}")
            self.repository = self.embedder = self.document_parser = None

    def start_collection(self, selected_folders: List[str]):
        if self.running: return
        self.selected_folders, self.running = selected_folders, True
        self.collection_thread = threading.Thread(target=self._collection_loop, daemon=True)
        self.collection_thread.start()
        print(f"사용자 {self.user_id}의 데이터 수집이 시작되었습니다.")
    
    def perform_initial_collection(self, selected_folders: List[str]):
        """초기 데이터 수집을 수행합니다."""
        if not self.repository:
            self.progress_message = "오류: RAG 시스템 초기화 실패"
            self.initial_collection_done = True
            return

        # 선택된 폴더를 나중에 백그라운드 수집에서도 사용할 수 있도록 저장합니다.
        self.selected_folders = selected_folders
        print("초기 데이터 수집을 시작합니다...")

        try:
            self.progress_message = "📁 초기 파일 수집 중..."
            files = self.file_collector.collect_files_from_drive(False, self, self.selected_folders)
            # collect_files_from_drive가 이미 80%로 설정함

            self.progress = 82.0
            self.progress_message = "🌐 브라우저 히스토리 수집 중..."
            history = self.browser_collector.collect_all_browser_history(False)

            self.progress = 87.0
            self.progress_message = "💾 파일 임베딩 생성 및 저장 중..."
            self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser)
            
            self.progress = 93.0
            self.progress_message = "💾 웹 콘텐츠 임베딩 생성 및 저장 중..."
            self.browser_collector.save_browser_history_to_db(history, self.repository, self.embedder)

            self.progress = 100.0
            self.progress_message = "🎉 초기 데이터 수집 완료!"

        except Exception as e:
            print(f"❌ 초기 데이터 수집 오류: {e}")
            self.progress_message = "오류 발생"
        finally:
            self.initial_collection_done = True
    
    def stop_collection(self):
        self.running = False
        if hasattr(self, 'collection_thread') and self.collection_thread: self.collection_thread.join()
        print(f"사용자 {self.user_id}의 데이터 수집이 중지되었습니다.")
    
    def _collection_loop(self):
        intervals = {'file': 3600, 'browser': 1800, 'app': 300, 'screen': 60}
        last_run = {key: 0 for key in intervals}
        while self.running:
            if not self.repository: time.sleep(10); continue
            current_time = time.time()
            if current_time - last_run['file'] >= intervals['file']: self._collect_files(); last_run['file'] = current_time
            if current_time - last_run['browser'] >= intervals['browser']: self._collect_browser_history(); last_run['browser'] = current_time
            if current_time - last_run['app'] >= intervals['app']: self._collect_active_apps(); last_run['app'] = current_time
            if current_time - last_run['screen'] >= intervals['screen']: self._collect_screen_activity(); last_run['screen'] = current_time
            time.sleep(10)

    def _collect_files(self):
        files = self.file_collector.collect_files_from_drive(True, self, self.selected_folders)
        self.file_collector.save_files_to_db(files, self.repository, self.embedder, self.document_parser)
    
    def _collect_browser_history(self):
        history = self.browser_collector.collect_all_browser_history(True)
        self.browser_collector.save_browser_history_to_db(history, self.repository, self.embedder)
    
    def _collect_active_apps(self):
        apps = self.app_collector.collect_active_applications()
        self.app_collector.save_active_apps_to_db(apps)
    
    def _collect_screen_activity(self):
        if (res := self.screen_collector.capture_screenshot()):
            image_data, file_path = res
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            analysis = loop.run_until_complete(self.screen_collector.analyze_screenshot_with_llm(image_data))
            loop.close()
            self.screen_collector.save_screen_activity_to_db(image_data, file_path, analysis, self.repository, self.embedder)
# -----------------------------------------------------------------------------
# 전역 관리 함수
# -----------------------------------------------------------------------------
data_collection_managers = {}
def get_manager(user_id: int) -> DataCollectionManager:
    if user_id not in data_collection_managers:
        data_collection_managers[user_id] = DataCollectionManager(user_id)
    return data_collection_managers[user_id]