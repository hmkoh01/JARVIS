"""
웹 콘텐츠 크롤러 모듈

URL에서 본문 텍스트(Main Content)만 깔끔하게 추출합니다.

Required packages:
    pip install trafilatura requests

Trafilatura는 웹 페이지에서 메인 콘텐츠만 추출하는 데 특화된 라이브러리입니다.
BeautifulSoup보다 노이즈(광고, 네비게이션 등)가 적습니다.
"""
import logging
from typing import Optional
import requests
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 타임아웃 설정 (초)
DEFAULT_TIMEOUT = 3
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
}

# 스킵할 URL 패턴 (키워드 추출 의미 없음)
SKIP_URL_PATTERNS = [
    'google.com/search',
    'bing.com/search',
    'naver.com/search',
    'daum.net/search',
    'youtube.com',
    'youtu.be',
    'facebook.com',
    'instagram.com',
    'twitter.com',
    'x.com',
    'linkedin.com',
    'tiktok.com',
    'login',
    'signin',
    'signup',
    'logout',
    'auth',
    'oauth',
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.zip',
    '.rar',
    '.exe',
    '.dmg',
]


def should_skip_url(url: str) -> bool:
    """
    키워드 추출 의미가 없는 URL인지 확인합니다.
    
    Args:
        url: 확인할 URL
        
    Returns:
        True면 스킵해야 함
    """
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in SKIP_URL_PATTERNS)


def fetch_web_content(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """
    URL에서 메인 콘텐츠 텍스트를 추출합니다.
    
    Trafilatura를 사용하여 광고, 네비게이션 등의 노이즈를 제거하고
    본문 텍스트만 깔끔하게 추출합니다.
    
    Args:
        url: 크롤링할 URL
        timeout: 요청 타임아웃 (초, 기본값: 3)
    
    Returns:
        추출된 본문 텍스트 또는 None (실패 시)
        
    Example:
        >>> text = fetch_web_content("https://example.com/article")
        >>> if text:
        ...     print(text[:200])
    """
    # URL 유효성 검사
    if not url or not url.startswith(('http://', 'https://')):
        return None
    
    # 스킵 패턴 확인
    if should_skip_url(url):
        logger.debug(f"URL 스킵 (패턴 매칭): {url}")
        return None
    
    try:
        # trafilatura로 콘텐츠 추출 시도
        return _fetch_with_trafilatura(url, timeout)
    except ImportError:
        # trafilatura가 없으면 BeautifulSoup 폴백
        logger.warning("trafilatura 미설치, BeautifulSoup으로 폴백합니다.")
        return _fetch_with_beautifulsoup(url, timeout)
    except Exception as e:
        logger.debug(f"웹 콘텐츠 추출 실패 ({url}): {e}")
        return None


def _fetch_with_trafilatura(url: str, timeout: int) -> Optional[str]:
    """
    Trafilatura를 사용하여 웹 콘텐츠를 추출합니다.
    """
    import trafilatura
    
    try:
        # HTTP 요청
        response = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        # 콘텐츠 타입 확인
        content_type = response.headers.get('content-type', '').lower()
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            logger.debug(f"HTML이 아닌 콘텐츠 타입: {content_type}")
            return None
        
        html_content = response.text
        
        # Trafilatura로 메인 콘텐츠 추출
        extracted = trafilatura.extract(
            html_content,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True
        )
        
        if extracted:
            # 텍스트 정제
            extracted = _clean_text(extracted)
            if len(extracted) >= 50:  # 최소 50자 이상
                return extracted
        
        return None
        
    except requests.Timeout:
        logger.debug(f"요청 타임아웃: {url}")
        return None
    except requests.RequestException as e:
        logger.debug(f"HTTP 요청 실패 ({url}): {e}")
        return None


def _fetch_with_beautifulsoup(url: str, timeout: int) -> Optional[str]:
    """
    BeautifulSoup을 사용하여 웹 콘텐츠를 추출합니다 (폴백).
    """
    from bs4 import BeautifulSoup
    
    try:
        response = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=timeout,
            allow_redirects=True
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 불필요한 요소 제거
        for element in soup(['script', 'style', 'nav', 'footer', 'header', 
                            'aside', 'form', 'iframe', 'noscript', 'svg']):
            element.decompose()
        
        # 텍스트 추출
        text = soup.get_text(separator='\n', strip=True)
        text = _clean_text(text)
        
        if len(text) >= 50:
            return text
        
        return None
        
    except Exception as e:
        logger.debug(f"BeautifulSoup 추출 실패 ({url}): {e}")
        return None


def _clean_text(text: str) -> str:
    """
    추출된 텍스트를 정제합니다.
    """
    import re
    
    # 연속된 공백/줄바꿈 정리
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    
    # 줄별로 정리
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if line and len(line) > 3:  # 너무 짧은 줄 제거
            lines.append(line)
    
    return '\n'.join(lines)


def get_url_domain(url: str) -> str:
    """
    URL에서 도메인을 추출합니다.
    
    Args:
        url: URL 문자열
        
    Returns:
        도메인 문자열
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def create_snippet(text: str, max_length: int = 200) -> str:
    """
    텍스트에서 스니펫(요약)을 생성합니다.
    
    Args:
        text: 원본 텍스트
        max_length: 최대 길이 (기본값: 200)
        
    Returns:
        잘린 텍스트 (말줄임표 포함)
    """
    if not text:
        return ""
    
    text = text.strip()
    
    if len(text) <= max_length:
        return text
    
    # max_length 근처에서 자연스럽게 자르기 (공백 기준)
    truncated = text[:max_length]
    last_space = truncated.rfind(' ')
    
    if last_space > max_length * 0.7:  # 70% 이상에서 공백 발견
        truncated = truncated[:last_space]
    
    return truncated.rstrip() + "..."


async def fetch_web_content_async(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """
    비동기로 URL에서 메인 콘텐츠 텍스트를 추출합니다.
    
    aiohttp를 사용하여 비동기 HTTP 요청을 수행합니다.
    
    Args:
        url: 크롤링할 URL
        timeout: 요청 타임아웃 (초)
    
    Returns:
        추출된 본문 텍스트 또는 None
    """
    import aiohttp
    import trafilatura
    
    if not url or not url.startswith(('http://', 'https://')):
        return None
    
    if should_skip_url(url):
        return None
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    return None
                
                html_content = await response.text()
                
                # Trafilatura로 메인 콘텐츠 추출
                extracted = trafilatura.extract(
                    html_content,
                    include_comments=False,
                    include_tables=False,
                    no_fallback=False,
                    favor_precision=True
                )
                
                if extracted:
                    extracted = _clean_text(extracted)
                    if len(extracted) >= 50:
                        return extracted
                
                return None
                
    except Exception as e:
        logger.debug(f"비동기 웹 콘텐츠 추출 실패 ({url}): {e}")
        return None

