"""
Docling 기반 문서 파서
다양한 문서 형식을 파싱하여 마크다운으로 변환하고,
재귀적 문자 분할(Recursive Character Splitting)을 통해 문맥을 보존하며 청킹합니다.
"""

import os
import yaml
import re
import warnings
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

PROJECT_ROOT = Path(__file__).parent.parent.parent

# PyTorch CUDA 관련 경고 억제 (Docling 모델 로딩 시 발생)
warnings.filterwarnings(
    'ignore', 
    message='.*Attempting to deserialize object on.*CUDA.*',
    category=UserWarning
)

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Docling이 설치되지 않았습니다. 기본 텍스트 파서를 사용합니다.")

logger = logging.getLogger(__name__)


class DocumentParser:
    """Docling 기반 문서 파서 및 스마트 청커"""
    
    def __init__(self, config_path: str = "configs.yaml"):
        absolute_config_path = PROJECT_ROOT / config_path
        self.config = self._load_config(absolute_config_path)
        self.docling_config = self.config.get('docling', {})
        self.export_type = self.docling_config.get('export_type', 'markdown')
        
        # 메모리 최적화 설정 로드
        self.use_cpu_only = self.docling_config.get('use_cpu_only', True)
        self.max_parallel_workers = self.docling_config.get('max_parallel_workers', 2)
        
        # GPU 메모리 부족 방지: CPU 모드 강제
        if self.use_cpu_only:
            os.environ['CUDA_VISIBLE_DEVICES'] = ''  # GPU 비활성화
            logger.info("🔧 Docling CPU 모드 활성화 (GPU 메모리 부족 방지)")
        
        # Docling 컨버터 초기화
        if DOCLING_AVAILABLE:
            converter_kwargs = self._build_converter_kwargs()
            self.converter = DocumentConverter(**converter_kwargs)
            logger.info("✅ Docling 문서 파서 초기화 완료 (커스텀 설정 적용)")
        else:
            self.converter = None
            logger.warning("⚠️ Docling이 없어 기본 파서를 사용합니다")
        
        # 지원 파일 확장자
        self.supported_extensions = {
            # 문서
            '.pdf', '.docx', '.doc', '.pptx', '.ppt',
            '.xlsx', '.xls', '.html', '.htm',
            '.txt', '.md', '.rst', '.rtf', '.odt',
            # 코드 및 설정 파일
            '.py', '.js', '.ts', '.jsx', '.tsx',
            '.java', '.cpp', '.c', '.h', '.cs',
            '.php', '.rb', '.go', '.rs', '.swift', '.kt',
            '.sh', '.bat', '.ps1',
            '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
            '.css', '.scss', '.sql',
            # 데이터
            '.csv', '.tsv'
        }
    
    def _load_config(self, config_path: str) -> dict:
        """설정 파일 로드"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            return {}
        except Exception as e:
            logger.error(f"설정 로드 오류: {e}")
            return {}

    def _build_converter_kwargs(self) -> Dict[str, Any]:
        """Docling 설정을 기반으로 DocumentConverter 초기화 파라미터를 생성합니다."""
        kwargs: Dict[str, Any] = {}

        pdf_config: Dict[str, Any] = self.docling_config.get('pdf', {}) if isinstance(self.docling_config, dict) else {}

        if pdf_config:
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import PdfFormatOption
            except ImportError:
                logger.warning("Docling PDF 설정을 적용하지 못했습니다 (필요한 모듈을 불러올 수 없음).")
            else:
                pdf_options = PdfPipelineOptions()

                if 'ocr_enabled' in pdf_config:
                    pdf_options.do_ocr = bool(pdf_config['ocr_enabled'])

                kwargs['format_options'] = {
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)
                }

        return kwargs
    
    def parse_document(self, file_path: str) -> Optional[str]:
        """
        문서를 파싱하여 마크다운 텍스트로 변환
        
        Args:
            file_path: 파싱할 문서 경로
        
        Returns:
            마크다운 형식의 텍스트 또는 None
        """
        try:
            file_ext = Path(file_path).suffix.lower()
            
            # 지원하지 않는 확장자
            if file_ext not in self.supported_extensions:
                logger.debug(f"지원하지 않는 파일 형식: {file_ext}")
                return None
            
            # Docling으로 파싱
            if self.converter and DOCLING_AVAILABLE:
                return self._parse_with_docling(file_path)
            else:
                # Fallback: 기본 텍스트 파서
                return self._parse_basic(file_path)
                
        except Exception as e:
            logger.error(f"문서 파싱 오류 {file_path}: {e}")
            return None
    
    def _parse_with_docling(self, file_path: str) -> Optional[str]:
        """Docling을 사용한 문서 파싱"""
        try:
            # Docling으로 문서 변환
            result = self.converter.convert(file_path)
            
            # 마크다운으로 export
            if self.export_type == 'markdown':
                return result.document.export_to_markdown()
            elif self.export_type == 'text':
                return result.document.export_to_text()
            else:
                # JSON 형식은 마크다운으로 변환
                return result.document.export_to_markdown()
                
        except Exception as e:
            logger.warning(f"Docling 파싱 오류(fallback 시도) {file_path}: {e}")
            # Fallback
            return self._parse_basic(file_path)
    
    def _parse_basic(self, file_path: str) -> Optional[str]:
        """기본 텍스트 파서 (Docling 없을 때)"""
        try:
            file_ext = Path(file_path).suffix.lower()
            
            # 텍스트 기반 파일 (일반 텍스트, 마크다운, 코드 파일 등)
            text_extensions = {
                '.txt', '.md', '.rst', '.csv', '.tsv',
                # 코드 파일
                '.py', '.js', '.ts', '.jsx', '.tsx',
                '.java', '.cpp', '.c', '.h', '.cs',
                '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.r', '.m',
                '.sh', '.bat', '.ps1',
                # 설정 및 데이터 파일
                '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
                '.css', '.scss', '.sql', '.html', '.htm'
            }
            
            if file_ext in text_extensions:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            
            # PDF 파일
            elif file_ext == '.pdf':
                try:
                    import PyPDF2
                    with open(file_path, 'rb') as f:
                        pdf_reader = PyPDF2.PdfReader(f)
                        text = ""
                        for page in pdf_reader.pages:
                            text += page.extract_text() + "\n"
                        return text
                except ImportError:
                    logger.warning("PyPDF2가 설치되지 않았습니다.")
                    return None
            
            # Word 문서
            elif file_ext in ['.docx', '.doc']:
                try:
                    from docx import Document
                    doc = Document(file_path)
                    text = ""
                    for paragraph in doc.paragraphs:
                        text += paragraph.text + "\n"
                    return text
                except ImportError:
                    logger.warning("python-docx가 설치되지 않았습니다.")
                    return None
            
            # Excel 파일
            elif file_ext in ['.xlsx', '.xls']:
                try:
                    import pandas as pd
                    df = pd.read_excel(file_path)
                    return df.to_markdown()
                except ImportError:
                    logger.warning("pandas가 설치되지 않았습니다.")
                    return None
            
            return None
            
        except Exception as e:
            logger.error(f"기본 파서 오류 {file_path}: {e}")
            return None
    
    def chunk_text(self, text: str, chunk_size: int = None, chunk_overlap: int = None) -> List[str]:
        """
        [개선됨] 재귀적 분할 방식 (Recursive Character Splitting)
        문단 -> 줄바꿈 -> 문장 -> 단어 순으로 의미 단위를 유지하며 분할합니다.
        
        Args:
            text: 분할할 텍스트
            chunk_size: 청크 크기 (문자 수, 기본값: 설정파일 또는 1000)
            chunk_overlap: 청크 오버랩 (문자 수, 기본값: 설정파일 또는 200)
        
        Returns:
            청크 리스트
        """
        # 설정값이 없으면 기본값 사용
        if chunk_size is None:
            chunk_size = self.docling_config.get('chunking', {}).get('chunk_size', 1000)
        if chunk_overlap is None:
            chunk_overlap = self.docling_config.get('chunking', {}).get('chunk_overlap', 200)
        
        if not text:
            return []

        # 1. 구분자 우선순위 정의 (문단 > 줄바꿈 > 문장 > 단어 > 문자)
        # "\n\n": 문단 구분
        # "\n": 줄바꿈
        # ". ": 문장 마침표 (뒤에 공백 포함)
        # " ": 단어 공백
        # "": 글자 단위 (최후의 수단)
        separators = ["\n\n", "\n", ". ", " ", ""]
        
        return self._recursive_split(text, separators, chunk_size, chunk_overlap)

    def _recursive_split(self, text: str, separators: List[str], chunk_size: int, chunk_overlap: int) -> List[str]:
        """
        재귀적으로 텍스트를 분할하고 병합하는 내부 로직
        """
        final_chunks = []
        
        # 현재 사용할 구분자 선택
        separator = separators[-1]
        new_separators = []
        
        for i, sep in enumerate(separators):
            if sep == "": # 마지막 단계 (글자 단위)
                separator = ""
                break
            if sep in text:
                separator = sep
                new_separators = separators[i + 1:]
                break
        
        # 구분자로 텍스트 1차 분할
        if separator:
            splits = text.split(separator)
        else:
            splits = list(text) # 글자 단위 분리

        # 병합을 위한 버퍼
        current_chunk = []
        current_length = 0
        separator_len = len(separator)
        
        for split in splits:
            split_len = len(split)
            
            # 현재 조각을 더했을 때 chunk_size를 초과하는지 확인
            if current_length + split_len + (separator_len if current_chunk else 0) > chunk_size:
                
                # 1. 현재까지 모은 청크 저장
                if current_chunk:
                    doc = separator.join(current_chunk)
                    if doc.strip():
                        final_chunks.append(doc)
                    
                    # 2. 오버랩 처리: 뒤에서부터 overlap 크기 내에 들어오는 요소만 남기고 버림
                    # (단순화를 위해, 오버랩 크기를 초과하는 앞부분을 제거)
                    while current_length > chunk_overlap and current_chunk:
                        removed = current_chunk.pop(0)
                        current_length -= (len(removed) + separator_len)
                        # 첫 요소 제거 시 separator 길이는 뺄 필요가 없을 수 있으나 근사치로 계산
                        if current_length < 0: current_length = 0

                # 3. 현재 조각이 chunk_size보다 크면 재귀적으로 더 잘게 쪼갬
                if split_len > chunk_size and new_separators:
                    sub_chunks = self._recursive_split(split, new_separators, chunk_size, chunk_overlap)
                    final_chunks.extend(sub_chunks)
                    # 재귀 분할된 조각들은 이미 처리되었으므로 현재 버퍼에 추가하지 않음
                    # 단, 마지막 서브청크가 오버랩을 위해 일부 필요할 수도 있으나 복잡도 감소를 위해 생략
                else:
                    # chunk_size보다는 작거나 더 이상 쪼갤 수 없는 경우
                    current_chunk.append(split)
                    current_length += split_len + separator_len
            else:
                # 버퍼에 추가 가능
                current_chunk.append(split)
                current_length += split_len + (separator_len if current_chunk else 0)
        
        # 남은 버퍼 처리
        if current_chunk:
            doc = separator.join(current_chunk)
            if doc.strip():
                final_chunks.append(doc)
                
        return final_chunks
    
    def parse_and_chunk(self, file_path: str) -> List[Dict[str, Any]]:
        """
        문서를 파싱하고 청크로 분할
        
        Args:
            file_path: 문서 경로
        
        Returns:
            청크 정보 리스트 [{'text': str, 'chunk_id': int, 'path': str}, ...]
        """
        try:
            # 문서 파싱
            content = self.parse_document(file_path)
            if not content:
                return []
            
            # 청크 분할 (개선된 방식 사용)
            chunks = self.chunk_text(content)
            
            # 청크 정보 생성
            chunk_infos = []
            for i, chunk in enumerate(chunks):
                chunk_infos.append({
                    'text': chunk,
                    'chunk_id': i,
                    'path': file_path,
                    'snippet': chunk[:200] + "..." if len(chunk) > 200 else chunk
                })
            
            return chunk_infos
            
        except Exception as e:
            logger.error(f"파싱 및 청크 분할 오류 {file_path}: {e}")
            return []