"""
Docling 기반 문서 파서
다양한 문서 형식을 파싱하여 마크다운으로 변환
"""

import os
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Docling이 설치되지 않았습니다. 기본 텍스트 파서를 사용합니다.")

logger = logging.getLogger(__name__)


class DocumentParser:
    """Docling 기반 문서 파서"""
    
    def __init__(self, config_path: str = "configs.yaml"):
        absolute_config_path = PROJECT_ROOT / config_path
        self.config = self._load_config(absolute_config_path)
        self.docling_config = self.config.get('docling', {})
        self.export_type = self.docling_config.get('export_type', 'markdown')
        
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
        텍스트를 청크로 분할
        
        Args:
            text: 분할할 텍스트
            chunk_size: 청크 크기 (문자 수)
            chunk_overlap: 청크 오버랩 (문자 수)
        
        Returns:
            청크 리스트
        """
        if chunk_size is None:
            chunk_size = self.docling_config.get('chunking', {}).get('chunk_size', 1000)
        if chunk_overlap is None:
            chunk_overlap = self.docling_config.get('chunking', {}).get('chunk_overlap', 200)
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            
            if chunk.strip():
                chunks.append(chunk)
            
            start = end - chunk_overlap
        
        return chunks
    
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
            
            # 청크 분할
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

