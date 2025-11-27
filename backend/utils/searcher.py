"""
웹 검색 유틸리티 모듈

DuckDuckGo를 사용하여 웹 검색을 수행합니다.
외부 API 키 없이 사용 가능합니다.

Required packages:
    pip install duckduckgo-search
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """검색 결과를 나타내는 데이터 클래스"""
    title: str
    url: str
    snippet: str


def search_web(
    query: str,
    max_results: int = 10,
    region: str = "kr-kr",
    safesearch: str = "moderate"
) -> List[SearchResult]:
    """
    DuckDuckGo를 사용하여 웹 검색을 수행합니다.
    
    Args:
        query: 검색 쿼리
        max_results: 최대 결과 수 (기본값: 10)
        region: 검색 지역 (기본값: kr-kr, 한국)
        safesearch: 세이프서치 수준 (off, moderate, strict)
    
    Returns:
        SearchResult 객체 리스트
    
    Example:
        >>> results = search_web("Python 머신러닝", max_results=5)
        >>> for r in results:
        ...     print(f"{r.title}: {r.url}")
    """
    results = []
    
    try:
        try:
            # 최신 버전: ddgs 사용
            from ddgs import DDGS
        except ImportError:
            # 구버전 호환: duckduckgo_search 사용
            from duckduckgo_search import DDGS
        
        with DDGS() as ddgs:
            # ddgs 패키지 API 호환성 처리
            # 에러 메시지에 따라 query를 required positional argument로 전달
            search_results = None
            last_error = None
            
            # 방법 1: query를 첫 번째 위치 인자로 전달 (최신 ddgs)
            try:
                search_results = list(ddgs.text(
                    query,
                    region=region,
                    safesearch=safesearch,
                    max_results=max_results
                ))
            except TypeError as e1:
                last_error = e1
                # 방법 2: query를 키워드 인자로 전달
                try:
                    search_results = list(ddgs.text(
                        query=query,
                        region=region,
                        safesearch=safesearch,
                        max_results=max_results
                    ))
                except (TypeError, Exception) as e2:
                    last_error = e2
                    # 방법 3: 구버전 API (keywords 파라미터 사용)
                    try:
                        search_results = list(ddgs.text(
                            keywords=query,
                            region=region,
                            safesearch=safesearch,
                            max_results=max_results
                        ))
                    except Exception as e3:
                        last_error = e3
                        raise
            
            if search_results is None:
                raise Exception(f"ddgs.text() 호출 실패: {last_error}")
            
            for item in search_results:
                result = SearchResult(
                    title=item.get('title', ''),
                    url=item.get('href', ''),
                    snippet=item.get('body', '')
                )
                if result.url:  # URL이 있는 결과만 추가
                    results.append(result)
        
        logger.info(f"검색 완료: '{query}' - {len(results)}개 결과")
        
    except ImportError:
        logger.error("duckduckgo-search 패키지가 설치되지 않았습니다. pip install duckduckgo-search")
    except Exception as e:
        logger.error(f"검색 중 오류 발생: {e}")
    
    return results


async def search_web_async(
    query: str,
    max_results: int = 10,
    region: str = "kr-kr",
    safesearch: str = "moderate"
) -> List[SearchResult]:
    """
    비동기로 DuckDuckGo 웹 검색을 수행합니다.
    
    Args:
        query: 검색 쿼리
        max_results: 최대 결과 수
        region: 검색 지역
        safesearch: 세이프서치 수준
    
    Returns:
        SearchResult 객체 리스트
    """
    results = []
    
    try:
        try:
            # 최신 버전: ddgs 사용
            from ddgs import DDGS
        except ImportError:
            # 구버전 호환: duckduckgo_search 사용
            from duckduckgo_search import DDGS
        
        with DDGS() as ddgs:
            # ddgs 패키지 API 호환성 처리
            # 에러 메시지에 따라 query를 required positional argument로 전달
            search_results = None
            last_error = None
            
            # 방법 1: query를 첫 번째 위치 인자로 전달 (최신 ddgs)
            try:
                search_results = list(ddgs.text(
                    query,
                    region=region,
                    safesearch=safesearch,
                    max_results=max_results
                ))
            except TypeError as e1:
                last_error = e1
                # 방법 2: query를 키워드 인자로 전달
                try:
                    search_results = list(ddgs.text(
                        query=query,
                        region=region,
                        safesearch=safesearch,
                        max_results=max_results
                    ))
                except (TypeError, Exception) as e2:
                    last_error = e2
                    # 방법 3: 구버전 API (keywords 파라미터 사용)
                    try:
                        search_results = list(ddgs.text(
                            keywords=query,
                            region=region,
                            safesearch=safesearch,
                            max_results=max_results
                        ))
                    except Exception as e3:
                        last_error = e3
                        raise
            
            if search_results is None:
                raise Exception(f"ddgs.text() 호출 실패: {last_error}")
            
            for item in search_results:
                result = SearchResult(
                    title=item.get('title', ''),
                    url=item.get('href', ''),
                    snippet=item.get('body', '')
                )
                if result.url:
                    results.append(result)
        
        logger.info(f"비동기 검색 완료: '{query}' - {len(results)}개 결과")
        
    except ImportError:
        logger.error("duckduckgo-search 패키지가 설치되지 않았습니다.")
    except Exception as e:
        logger.error(f"비동기 검색 중 오류 발생: {e}")
    
    return results


def search_multiple_queries(
    queries: List[str],
    max_results_per_query: int = 5
) -> Dict[str, List[SearchResult]]:
    """
    여러 쿼리에 대해 동시에 검색을 수행합니다.
    
    Args:
        queries: 검색 쿼리 리스트
        max_results_per_query: 각 쿼리당 최대 결과 수
    
    Returns:
        쿼리를 키로, SearchResult 리스트를 값으로 하는 딕셔너리
    """
    results = {}
    
    for query in queries:
        query_results = search_web(query, max_results=max_results_per_query)
        results[query] = query_results
    
    return results


def deduplicate_results(results: List[SearchResult]) -> List[SearchResult]:
    """
    URL 기준으로 중복 결과를 제거합니다.
    
    Args:
        results: SearchResult 리스트
    
    Returns:
        중복이 제거된 SearchResult 리스트
    """
    seen_urls = set()
    unique_results = []
    
    for result in results:
        # URL 정규화 (후행 슬래시 제거)
        normalized_url = result.url.rstrip('/')
        
        if normalized_url not in seen_urls:
            seen_urls.add(normalized_url)
            unique_results.append(result)
    
    return unique_results

