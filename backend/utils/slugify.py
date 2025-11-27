"""
파일명 생성을 위한 Slugify 유틸리티

한국어와 영어를 모두 지원하며, 파일 시스템에 안전한 문자열을 생성합니다.
"""

import re
import unicodedata
from typing import Optional


def slugify(
    text: str,
    max_length: int = 50,
    separator: str = "_",
    allow_unicode: bool = True
) -> str:
    """
    텍스트를 파일 시스템에 안전한 슬러그로 변환합니다.
    
    Args:
        text: 변환할 텍스트
        max_length: 최대 길이 (기본값: 50)
        separator: 공백 대체 문자 (기본값: _)
        allow_unicode: 유니코드(한국어 등) 허용 여부 (기본값: True)
    
    Returns:
        슬러그화된 문자열
    
    Example:
        >>> slugify("Python 머신러닝 튜토리얼")
        'Python_머신러닝_튜토리얼'
        >>> slugify("Hello World!", allow_unicode=False)
        'hello_world'
    """
    if not text:
        return "untitled"
    
    # 유니코드 정규화
    text = unicodedata.normalize('NFKC', text)
    
    if allow_unicode:
        # 한국어, 영어, 숫자, 일부 특수문자만 허용
        # 파일 시스템에서 금지된 문자 제거: \ / : * ? " < > |
        text = re.sub(r'[\\/:*?"<>|]', '', text)
        # 연속된 공백을 하나로
        text = re.sub(r'\s+', ' ', text)
        # 앞뒤 공백 제거
        text = text.strip()
        # 공백을 separator로 변환
        text = text.replace(' ', separator)
    else:
        # ASCII만 허용하는 전통적인 슬러그
        text = unicodedata.normalize('NFKD', text)
        text = text.encode('ascii', 'ignore').decode('ascii')
        text = text.lower()
        # 영숫자와 하이픈/언더스코어만 허용
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', separator, text)
        text = text.strip(separator)
    
    # 연속된 separator 제거
    text = re.sub(f'{re.escape(separator)}+', separator, text)
    
    # 최대 길이 제한
    if len(text) > max_length:
        text = text[:max_length].rstrip(separator)
    
    return text or "untitled"


def generate_filename(
    keyword: str,
    timestamp: Optional[str] = None,
    extension: str = ".pdf"
) -> str:
    """
    키워드와 타임스탬프를 조합하여 파일명을 생성합니다.
    
    Args:
        keyword: 주요 키워드
        timestamp: 타임스탬프 문자열 (없으면 현재 시각 사용)
        extension: 파일 확장자 (기본값: .pdf)
    
    Returns:
        파일명 문자열
    
    Example:
        >>> generate_filename("Python 입문", "20240101_120000")
        '20240101_120000_Python_입문.pdf'
    """
    from datetime import datetime
    
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 키워드 슬러그화
    keyword_slug = slugify(keyword, max_length=40)
    
    # 확장자 정리
    if not extension.startswith('.'):
        extension = '.' + extension
    
    return f"{timestamp}_{keyword_slug}{extension}"

