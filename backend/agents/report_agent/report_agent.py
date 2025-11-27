"""
ReportAgent - 웹 검색 기반 보고서 생성 에이전트

검색 계획 → 웹 검색/크롤링 → 정보 정제 → 보고서 작성 → PDF 저장
파이프라인을 통해 구조화된 보고서를 생성합니다.

사용 모델: Gemini 2.5 Pro
저장 경로: ~/Documents/JARVIS/Reports/
"""

import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

import google.generativeai as genai

from ..base_agent import BaseAgent, AgentResponse
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class SourceInfo:
    """출처 정보를 나타내는 데이터 클래스"""
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    relevance_score: float = 0.0


@dataclass 
class ReportResult:
    """보고서 생성 결과"""
    success: bool
    summary: str = ""
    pdf_path: str = ""
    pdf_filename: str = ""
    sources: List[Dict[str, str]] = field(default_factory=list)
    message: str = ""


class ReportAgent(BaseAgent):
    """
    웹 검색 기반 보고서 생성 에이전트
    
    Pipeline:
        1. Search Planning: LLM으로 세부 쿼리 생성
        2. Web Search: DuckDuckGo로 검색
        3. Crawling: 상위 URL에서 본문 추출
        4. Context Filtering: 핵심 문단 필터링
        5. Report Generation: Markdown 보고서 작성
        6. PDF Export: PDF로 변환 및 저장
    """
    
    # 기본 설정
    MAX_SEARCH_RESULTS_PER_QUERY = 5
    MAX_CRAWL_URLS = 10
    MAX_CONTENT_LENGTH = 3000  # 각 문서당 최대 문자 수
    MAX_TOTAL_CONTEXT = 15000  # 전체 컨텍스트 최대 문자 수
    
    def __init__(self):
        super().__init__(
            agent_type="report",
            description="웹 검색을 통해 정보를 수집하고 구조화된 보고서를 생성합니다."
        )
        self._init_llm()
        self._init_report_dir()
    
    def _init_llm(self):
        """Gemini LLM 클라이언트 초기화"""
        self.llm_available = False
        
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY가 설정되지 않아 ReportAgent LLM 기능을 사용할 수 없습니다.")
            return
        
        try:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            
            # Safety settings
            self.safety_settings = [
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "block_none"},
            ]
            
            # JSON 출력용 모델 (검색 계획 등)
            self.llm_model_json = genai.GenerativeModel(
                model_name="gemini-2.5-pro",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_output_tokens": 2048,
                    "response_mime_type": "application/json",
                },
                safety_settings=self.safety_settings,
            )
            
            # 텍스트 출력용 모델 (보고서 생성)
            self.llm_model_text = genai.GenerativeModel(
                model_name="gemini-2.5-pro",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 8192,
                    "response_mime_type": "text/plain",
                },
                safety_settings=self.safety_settings,
            )
            
            self.llm_available = True
            logger.info("ReportAgent: Gemini LLM 클라이언트 초기화 완료")
            
        except Exception as e:
            logger.error(f"ReportAgent: Gemini LLM 초기화 오류: {e}")
    
    def _init_report_dir(self):
        """보고서 저장 디렉터리 초기화"""
        # settings에서 REPORTS_DIR 사용 (기본값: ~/Documents/JARVIS/Reports)
        self.report_dir = Path(settings.REPORTS_DIR)
        
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"ReportAgent: 보고서 저장 경로: {self.report_dir}")
        except Exception as e:
            logger.error(f"ReportAgent: 보고서 디렉터리 생성 실패: {e}")
            # Fallback to temp directory
            import tempfile
            self.report_dir = Path(tempfile.gettempdir()) / "JARVIS_Reports"
            self.report_dir.mkdir(parents=True, exist_ok=True)
    
    # ============================================================
    # BaseAgent Interface Implementation
    # ============================================================
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """동기 처리 메서드 (langgraph 호환)"""
        question = state.get("question", "")
        user_id = state.get("user_id")
        
        if not question:
            return {
                **state,
                "answer": "보고서 주제가 제공되지 않았습니다.",
                "success": False,
                "agent_type": self.agent_type
            }
        
        # 비동기 함수를 동기적으로 실행
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 이벤트 루프가 실행 중이면 새 태스크 생성
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.create_report(user_id, question)
                    )
                    result = future.result(timeout=180)
            else:
                result = loop.run_until_complete(
                    self.create_report(user_id, question)
                )
        except Exception as e:
            logger.exception(f"ReportAgent process 오류: {e}")
            result = {
                "success": False,
                "summary": "",
                "message": f"보고서 생성 중 오류: {str(e)}"
            }
        
        return {
            **state,
            "answer": result.get("summary", "보고서 생성에 실패했습니다."),
            "success": result.get("success", False),
            "agent_type": self.agent_type,
            "metadata": {
                "pdf_path": result.get("pdf_path", ""),
                "pdf_filename": result.get("pdf_filename", ""),
                "sources": result.get("sources", [])
            }
        }
    
    async def process_async(self, user_input: str, user_id: Optional[int] = None) -> AgentResponse:
        """비동기 처리 메서드"""
        result = await self.create_report(user_id, user_input)
        
        return AgentResponse(
            success=result.get("success", False),
            content=result.get("summary", ""),
            agent_type=self.agent_type,
            metadata={
                "pdf_path": result.get("pdf_path", ""),
                "pdf_filename": result.get("pdf_filename", ""),
                "sources": result.get("sources", []),
                "message": result.get("message", "")
            }
        )
    
    # ============================================================
    # Main Public Method
    # ============================================================
    
    async def create_report(
        self,
        user_id: Optional[int],
        keyword: str,
        recommendation_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        보고서 생성 메인 메서드
        
        Args:
            user_id: 사용자 ID
            keyword: 보고서 주제 키워드
            recommendation_id: 연관된 추천 ID (선택)
        
        Returns:
            {
                "success": bool,
                "summary": str,
                "pdf_path": str,
                "pdf_filename": str,
                "sources": [{"title": str, "url": str}, ...],
                "message": str (선택)
            }
        """
        logger.info(f"ReportAgent: 보고서 생성 시작 - keyword='{keyword}', user_id={user_id}")
        
        try:
            if not self.llm_available:
                return {
                    "success": False,
                    "summary": "",
                    "pdf_path": "",
                    "pdf_filename": "",
                    "sources": [],
                    "message": "LLM 서비스를 사용할 수 없습니다. GEMINI_API_KEY를 확인해주세요."
                }
            
            # ==============================
            # Step 1: Search Planning
            # ==============================
            logger.info("ReportAgent: Step 1 - 검색 계획 수립")
            sub_queries = await self._plan_search_queries(keyword)
            
            if not sub_queries or len(sub_queries) < 2:
                logger.warning("ReportAgent: 검색 쿼리 생성 실패, 기본 쿼리 사용")
                sub_queries = [
                    f"{keyword} 개요",
                    f"{keyword} 특징",
                    f"{keyword} 활용 사례",
                    f"{keyword} 비교 분석"
                ]
            
            logger.info(f"ReportAgent: 생성된 검색 쿼리: {sub_queries}")
            
            # ==============================
            # Step 2: Web Search
            # ==============================
            logger.info("ReportAgent: Step 2 - 웹 검색")
            search_results = await self._perform_web_search(sub_queries)
            
            if not search_results:
                return {
                    "success": False,
                    "summary": "",
                    "pdf_path": "",
                    "pdf_filename": "",
                    "sources": [],
                    "message": "검색 결과를 찾을 수 없습니다."
                }
            
            logger.info(f"ReportAgent: 검색 결과 {len(search_results)}개 URL 확보")
            
            # ==============================
            # Step 3: Crawling
            # ==============================
            logger.info("ReportAgent: Step 3 - 웹 크롤링")
            crawled_sources = await self._crawl_urls(search_results)
            
            if not crawled_sources:
                logger.warning("ReportAgent: 크롤링 결과 없음, 검색 스니펫만 사용")
                crawled_sources = [
                    SourceInfo(
                        title=r.title,
                        url=r.url,
                        snippet=r.snippet,
                        content=r.snippet
                    )
                    for r in search_results[:self.MAX_CRAWL_URLS]
                ]
            
            logger.info(f"ReportAgent: 크롤링 완료 - {len(crawled_sources)}개 문서")
            
            # ==============================
            # Step 4: Context Filtering
            # ==============================
            logger.info("ReportAgent: Step 4 - 컨텍스트 필터링")
            filtered_sources = await self._filter_context(crawled_sources, keyword)
            logger.info(f"ReportAgent: 필터링 완료 - {len(filtered_sources)}개 핵심 문서")
            
            # ==============================
            # Step 5: Report Generation
            # ==============================
            logger.info("ReportAgent: Step 5 - 보고서 생성")
            markdown_report = await self._generate_report(keyword, filtered_sources)
            
            if not markdown_report:
                return {
                    "success": False,
                    "summary": "",
                    "pdf_path": "",
                    "pdf_filename": "",
                    "sources": [],
                    "message": "보고서 내용 생성에 실패했습니다."
                }
            
            # ==============================
            # Step 6: PDF Export
            # ==============================
            logger.info("ReportAgent: Step 6 - PDF 저장")
            pdf_path, pdf_filename, export_success = await self._export_to_pdf(
                keyword, markdown_report
            )
            
            # 출처 정보 정리
            sources = [
                {"title": s.title, "url": s.url}
                for s in filtered_sources
            ]
            
            # 요약 생성
            summary = f"'{keyword}'에 대한 보고서가 생성되었습니다."
            if export_success:
                summary += f" PDF 파일이 저장되었습니다: {pdf_filename}"
            else:
                summary += " (PDF 변환 실패, Markdown 파일로 저장됨)"
            
            logger.info(f"ReportAgent: 보고서 생성 완료 - {pdf_path}")
            
            return {
                "success": True,
                "summary": summary,
                "pdf_path": str(pdf_path),
                "pdf_filename": pdf_filename,
                "sources": sources,
                "message": ""
            }
            
        except Exception as e:
            logger.exception(f"ReportAgent: 보고서 생성 중 오류: {e}")
            return {
                "success": False,
                "summary": "",
                "pdf_path": "",
                "pdf_filename": "",
                "sources": [],
                "message": f"보고서 생성 중 오류가 발생했습니다: {str(e)}"
            }
    
    # ============================================================
    # Pipeline Step 1: Search Planning
    # ============================================================
    
    async def _plan_search_queries(self, keyword: str) -> List[str]:
        """
        LLM을 사용하여 세부 검색 쿼리를 생성합니다.
        
        Args:
            keyword: 주제 키워드
        
        Returns:
            검색 쿼리 문자열 리스트 (최소 4개)
        """
        prompt = f"""당신은 리서치 전문가입니다. 다음 주제에 대한 종합적인 보고서를 작성하기 위해 
웹 검색에 사용할 세부 쿼리 목록을 생성해주세요.

## 주제
{keyword}

## 요구사항
1. 최소 4개, 최대 6개의 검색 쿼리를 생성하세요.
2. 각 쿼리는 다른 측면을 다뤄야 합니다:
   - 기본 정의와 개요
   - 주요 특징과 기능
   - 활용 사례나 적용 분야
   - 경쟁 제품/기술과의 비교
   - 최신 동향이나 뉴스
   - 장단점 분석
3. 한국어로 검색할 쿼리와 영어로 검색할 쿼리를 적절히 섞어주세요.
4. 구체적이고 검색에 효과적인 쿼리를 작성하세요.

## 출력 형식
반드시 JSON 배열 형식으로 출력하세요:
["쿼리1", "쿼리2", "쿼리3", "쿼리4", ...]

예시:
["Python 머신러닝 개요", "Python machine learning tutorial", "Python ML 라이브러리 비교", "Python 머신러닝 활용 사례"]
"""

        try:
            response = self.llm_model_json.generate_content(
                prompt,
                request_options={"timeout": 30}
            )
            
            response_text = self._extract_llm_response(response)
            if not response_text:
                return []
            
            # JSON 파싱
            queries = json.loads(response_text)
            
            if isinstance(queries, list) and len(queries) >= 4:
                return queries[:6]  # 최대 6개
            
            return []
            
        except json.JSONDecodeError as e:
            logger.error(f"ReportAgent: 검색 계획 JSON 파싱 오류: {e}")
            return []
        except Exception as e:
            logger.error(f"ReportAgent: 검색 계획 생성 오류: {e}")
            return []
    
    # ============================================================
    # Pipeline Step 2: Web Search
    # ============================================================
    
    async def _perform_web_search(self, queries: List[str]) -> List:
        """
        여러 쿼리로 웹 검색을 수행합니다.
        
        Args:
            queries: 검색 쿼리 리스트
        
        Returns:
            중복 제거된 SearchResult 리스트
        """
        try:
            from utils.searcher import search_web, deduplicate_results, SearchResult
        except ImportError:
            logger.error("ReportAgent: searcher 모듈을 찾을 수 없습니다.")
            return []
        
        all_results = []
        
        for query in queries:
            try:
                results = search_web(
                    query,
                    max_results=self.MAX_SEARCH_RESULTS_PER_QUERY
                )
                all_results.extend(results)
                logger.debug(f"ReportAgent: '{query}' 검색 - {len(results)}개 결과")
            except Exception as e:
                logger.warning(f"ReportAgent: 쿼리 '{query}' 검색 실패: {e}")
                continue
        
        # 중복 제거
        unique_results = deduplicate_results(all_results)
        
        return unique_results[:self.MAX_CRAWL_URLS]
    
    # ============================================================
    # Pipeline Step 3: Crawling
    # ============================================================
    
    async def _crawl_urls(self, search_results: List) -> List[SourceInfo]:
        """
        검색 결과 URL에서 본문을 크롤링합니다.
        
        Args:
            search_results: SearchResult 리스트
        
        Returns:
            SourceInfo 리스트
        """
        try:
            from utils.web_crawler import fetch_web_content
        except ImportError:
            logger.error("ReportAgent: web_crawler 모듈을 찾을 수 없습니다.")
            return []
        
        sources = []
        
        for result in search_results:
            try:
                content = fetch_web_content(result.url, timeout=5)
                
                if content:
                    # 내용이 너무 길면 자르기
                    if len(content) > self.MAX_CONTENT_LENGTH:
                        content = content[:self.MAX_CONTENT_LENGTH] + "..."
                    
                    sources.append(SourceInfo(
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        content=content
                    ))
                    logger.debug(f"ReportAgent: 크롤링 성공 - {result.url}")
                else:
                    # 크롤링 실패 시 스니펫 사용
                    sources.append(SourceInfo(
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        content=result.snippet
                    ))
                    
            except Exception as e:
                logger.debug(f"ReportAgent: 크롤링 실패 {result.url}: {e}")
                # 실패해도 스니펫은 추가
                sources.append(SourceInfo(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    content=result.snippet
                ))
        
        return sources
    
    # ============================================================
    # Pipeline Step 4: Context Filtering
    # ============================================================
    
    async def _filter_context(
        self,
        sources: List[SourceInfo],
        keyword: str
    ) -> List[SourceInfo]:
        """
        컨텍스트를 필터링하여 핵심 문단만 남깁니다.
        간단한 키워드 기반 관련도 점수를 계산합니다.
        
        Args:
            sources: SourceInfo 리스트
            keyword: 주제 키워드
        
        Returns:
            필터링된 SourceInfo 리스트
        """
        if not sources:
            return []
        
        # 키워드를 단어로 분리
        keywords = keyword.lower().split()
        
        for source in sources:
            content_lower = source.content.lower()
            
            # 간단한 TF 기반 점수 계산
            score = 0.0
            for kw in keywords:
                # 키워드 출현 횟수
                count = content_lower.count(kw)
                score += min(count * 0.1, 1.0)  # 최대 1.0
            
            # 내용 길이 보너스 (너무 짧지 않은 것 선호)
            if len(source.content) > 200:
                score += 0.2
            
            source.relevance_score = score
        
        # 점수 기준 정렬
        sorted_sources = sorted(sources, key=lambda x: x.relevance_score, reverse=True)
        
        # 전체 컨텍스트 크기 제한
        filtered = []
        total_length = 0
        
        for source in sorted_sources:
            if total_length + len(source.content) <= self.MAX_TOTAL_CONTEXT:
                filtered.append(source)
                total_length += len(source.content)
            else:
                # 남은 공간에 맞게 내용 자르기
                remaining = self.MAX_TOTAL_CONTEXT - total_length
                if remaining > 500:
                    source.content = source.content[:remaining] + "..."
                    filtered.append(source)
                break
        
        return filtered
    
    # ============================================================
    # Pipeline Step 5: Report Generation
    # ============================================================
    
    async def _generate_report(
        self,
        keyword: str,
        sources: List[SourceInfo]
    ) -> Optional[str]:
        """
        LLM을 사용하여 Markdown 보고서를 생성합니다.
        
        Args:
            keyword: 주제 키워드
            sources: 필터링된 SourceInfo 리스트
        
        Returns:
            Markdown 형식의 보고서 문자열
        """
        # 출처 정보와 컨텍스트 준비
        context_parts = []
        references = []
        
        for i, source in enumerate(sources, 1):
            context_parts.append(f"[{i}] {source.title}\nURL: {source.url}\n내용:\n{source.content}\n")
            references.append(f"[{i}] {source.title} - {source.url}")
        
        context_text = "\n---\n".join(context_parts)
        references_text = "\n".join(references)
        
        prompt = f"""당신은 전문 리서치 보고서 작성자입니다. 
주어진 정보를 바탕으로 '{keyword}'에 대한 종합적인 보고서를 작성해주세요.

## 수집된 정보
{context_text}

## 출처 목록
{references_text}

## 보고서 작성 가이드라인

1. **구조**: 다음 구조를 따르세요:
   - **개요**: 주제에 대한 간략한 소개와 정의
   - **주요 특징**: 핵심 특징이나 기능 설명
   - **경쟁 비교**: 유사 기술/제품과의 비교 (가능한 경우)
   - **활용 분야**: 실제 적용 사례나 활용 분야
   - **참고 문헌**: 사용된 출처 목록

2. **인용**: 정보를 인용할 때 문장 끝에 [1], [2] 형식으로 출처를 표시하세요.

3. **형식**: 
   - Markdown 형식으로 작성
   - 적절한 헤딩(#, ##, ###) 사용
   - 불릿 포인트와 번호 목록 활용
   - 중요한 내용은 **굵게** 표시

4. **언어**: 한국어로 작성하되, 기술 용어는 영어 원어를 병기

5. **품질**: 
   - 명확하고 이해하기 쉽게 작성
   - 수집된 정보를 바탕으로 정확하게 작성
   - 추측성 내용은 피하고, 확인된 정보만 포함

## 출력
Markdown 형식의 보고서만 출력하세요. 추가 설명이나 메타 코멘트는 포함하지 마세요.
"""

        try:
            response = self.llm_model_text.generate_content(
                prompt,
                request_options={"timeout": 180}  # 보고서 생성은 시간이 오래 걸리므로 타임아웃 증가
            )
            
            report_text = self._extract_llm_response(response)
            
            if report_text:
                # 보고서 끝에 참고 문헌 섹션이 없으면 추가
                if "참고 문헌" not in report_text and "References" not in report_text:
                    report_text += f"\n\n---\n\n## 참고 문헌\n\n{references_text}"
                
                # 생성 정보 추가
                timestamp = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
                report_text += f"\n\n---\n\n*이 보고서는 {timestamp}에 JARVIS ReportAgent에 의해 자동 생성되었습니다.*"
                
                return report_text
            
            return None
            
        except Exception as e:
            logger.error(f"ReportAgent: 보고서 생성 오류: {e}")
            return None
    
    # ============================================================
    # Pipeline Step 6: PDF Export
    # ============================================================
    
    async def _export_to_pdf(
        self,
        keyword: str,
        markdown_content: str
    ) -> Tuple[str, str, bool]:
        """
        Markdown을 PDF로 변환하여 저장합니다.
        
        Args:
            keyword: 주제 키워드
            markdown_content: Markdown 보고서 내용
        
        Returns:
            (파일 경로, 파일명, 성공 여부)
        """
        from utils.slugify import generate_filename
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # PDF 변환 시도
        pdf_success = False
        
        # 방법 1: reportlab 시도
        try:
            pdf_filename = generate_filename(keyword, timestamp, ".pdf")
            pdf_path = self.report_dir / pdf_filename
            
            pdf_success = self._convert_markdown_to_pdf_reportlab(
                markdown_content, str(pdf_path)
            )
            
            if pdf_success:
                logger.info(f"ReportAgent: reportlab으로 PDF 생성 성공")
                return str(pdf_path), pdf_filename, True
                
        except Exception as e:
            logger.warning(f"ReportAgent: reportlab PDF 변환 실패: {e}")
        
        # 방법 2: markdown2 + xhtml2pdf 시도
        try:
            pdf_filename = generate_filename(keyword, timestamp, ".pdf")
            pdf_path = self.report_dir / pdf_filename
            
            pdf_success = self._convert_markdown_to_pdf_xhtml2pdf(
                markdown_content, str(pdf_path)
            )
            
            if pdf_success:
                logger.info(f"ReportAgent: xhtml2pdf로 PDF 생성 성공")
                return str(pdf_path), pdf_filename, True
                
        except Exception as e:
            logger.warning(f"ReportAgent: xhtml2pdf PDF 변환 실패: {e}")
        
        # 방법 3: Markdown 파일로 저장 (폴백)
        try:
            md_filename = generate_filename(keyword, timestamp, ".md")
            md_path = self.report_dir / md_filename
            
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            logger.info(f"ReportAgent: Markdown 파일로 저장: {md_path}")
            return str(md_path), md_filename, False
            
        except Exception as e:
            logger.error(f"ReportAgent: Markdown 저장도 실패: {e}")
            return "", "", False
    
    def _convert_markdown_to_pdf_reportlab(
        self,
        markdown_content: str,
        output_path: str
    ) -> bool:
        """reportlab을 사용하여 PDF 생성"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
            import re
            
            # 한국어 폰트 등록 시도
            font_registered = False
            font_name = "Helvetica"  # 기본 폰트
            
            # Windows에서 맑은 고딕 폰트 찾기
            possible_fonts = [
                ("C:/Windows/Fonts/malgun.ttf", "MalgunGothic"),
                ("C:/Windows/Fonts/NanumGothic.ttf", "NanumGothic"),
                ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "NanumGothic"),
                ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "AppleGothic"),
            ]
            
            for font_path, name in possible_fonts:
                if Path(font_path).exists():
                    try:
                        pdfmetrics.registerFont(TTFont(name, font_path))
                        font_name = name
                        font_registered = True
                        break
                    except Exception:
                        continue
            
            # 스타일 설정
            styles = getSampleStyleSheet()
            
            # 커스텀 스타일 생성
            styles.add(ParagraphStyle(
                name='CustomTitle',
                parent=styles['Title'],
                fontName=font_name,
                fontSize=18,
                spaceAfter=20,
            ))
            
            styles.add(ParagraphStyle(
                name='CustomHeading1',
                parent=styles['Heading1'],
                fontName=font_name,
                fontSize=14,
                spaceBefore=15,
                spaceAfter=10,
            ))
            
            styles.add(ParagraphStyle(
                name='CustomHeading2',
                parent=styles['Heading2'],
                fontName=font_name,
                fontSize=12,
                spaceBefore=12,
                spaceAfter=8,
            ))
            
            styles.add(ParagraphStyle(
                name='CustomBody',
                parent=styles['Normal'],
                fontName=font_name,
                fontSize=10,
                alignment=TA_JUSTIFY,
                spaceBefore=6,
                spaceAfter=6,
                leading=14,
            ))
            
            styles.add(ParagraphStyle(
                name='CustomBullet',
                parent=styles['Normal'],
                fontName=font_name,
                fontSize=10,
                leftIndent=20,
                spaceBefore=3,
                spaceAfter=3,
            ))
            
            # PDF 문서 생성
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=2*cm,
                leftMargin=2*cm,
                topMargin=2*cm,
                bottomMargin=2*cm
            )
            
            # Markdown을 파싱하여 PDF 요소로 변환
            story = []
            lines = markdown_content.split('\n')
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                if not line:
                    story.append(Spacer(1, 6))
                    i += 1
                    continue
                
                # 제목 처리
                if line.startswith('# '):
                    text = self._clean_markdown_text(line[2:])
                    story.append(Paragraph(text, styles['CustomTitle']))
                elif line.startswith('## '):
                    text = self._clean_markdown_text(line[3:])
                    story.append(Paragraph(text, styles['CustomHeading1']))
                elif line.startswith('### '):
                    text = self._clean_markdown_text(line[4:])
                    story.append(Paragraph(text, styles['CustomHeading2']))
                elif line.startswith('- ') or line.startswith('* '):
                    # 불릿 포인트
                    text = self._clean_markdown_text(line[2:])
                    story.append(Paragraph(f"• {text}", styles['CustomBullet']))
                elif re.match(r'^\d+\.', line):
                    # 번호 목록
                    text = self._clean_markdown_text(line)
                    story.append(Paragraph(text, styles['CustomBullet']))
                elif line.startswith('---'):
                    story.append(Spacer(1, 10))
                elif line.startswith('*') and line.endswith('*'):
                    # 이탤릭 (메타 정보)
                    text = self._clean_markdown_text(line.strip('*'))
                    story.append(Paragraph(f"<i>{text}</i>", styles['CustomBody']))
                else:
                    # 일반 문단
                    text = self._clean_markdown_text(line)
                    if text:
                        story.append(Paragraph(text, styles['CustomBody']))
                
                i += 1
            
            # PDF 생성
            doc.build(story)
            return True
            
        except ImportError:
            logger.warning("ReportAgent: reportlab이 설치되지 않았습니다.")
            return False
        except Exception as e:
            logger.error(f"ReportAgent: reportlab PDF 생성 오류: {e}")
            return False
    
    def _convert_markdown_to_pdf_xhtml2pdf(
        self,
        markdown_content: str,
        output_path: str
    ) -> bool:
        """markdown2 + xhtml2pdf를 사용하여 PDF 생성"""
        try:
            import markdown2
            from xhtml2pdf import pisa
            
            # Markdown을 HTML로 변환
            html_content = markdown2.markdown(
                markdown_content,
                extras=['fenced-code-blocks', 'tables', 'header-ids']
            )
            
            # HTML 템플릿
            html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        @page {{
            size: A4;
            margin: 2cm;
        }}
        body {{
            font-family: 'Malgun Gothic', 'NanumGothic', 'Apple SD Gothic Neo', sans-serif;
            font-size: 11pt;
            line-height: 1.6;
            color: #333;
        }}
        h1 {{
            font-size: 20pt;
            color: #1a1a1a;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
            margin-top: 20px;
        }}
        h2 {{
            font-size: 16pt;
            color: #2c2c2c;
            margin-top: 18px;
        }}
        h3 {{
            font-size: 13pt;
            color: #3c3c3c;
            margin-top: 15px;
        }}
        p {{
            text-align: justify;
            margin: 10px 0;
        }}
        ul, ol {{
            margin-left: 20px;
        }}
        li {{
            margin: 5px 0;
        }}
        code {{
            background-color: #f4f4f4;
            padding: 2px 5px;
            border-radius: 3px;
            font-family: 'Consolas', monospace;
        }}
        hr {{
            border: none;
            border-top: 1px solid #ccc;
            margin: 20px 0;
        }}
        em {{
            color: #666;
        }}
    </style>
</head>
<body>
{html_content}
</body>
</html>
"""
            
            # PDF 생성
            with open(output_path, 'wb') as pdf_file:
                pisa_status = pisa.CreatePDF(
                    html_template.encode('utf-8'),
                    dest=pdf_file,
                    encoding='utf-8'
                )
            
            return not pisa_status.err
            
        except ImportError as e:
            logger.warning(f"ReportAgent: xhtml2pdf 또는 markdown2가 설치되지 않았습니다: {e}")
            return False
        except Exception as e:
            logger.error(f"ReportAgent: xhtml2pdf PDF 생성 오류: {e}")
            return False
    
    def _clean_markdown_text(self, text: str) -> str:
        """Markdown 텍스트에서 특수 문자를 정리합니다."""
        import re
        
        # 굵은 글씨 변환
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        # 이탤릭 변환
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        # 인라인 코드 변환
        text = re.sub(r'`(.+?)`', r'<font face="Courier">\1</font>', text)
        # 링크 제거 (텍스트만 유지)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # XML 특수문자 이스케이프
        text = text.replace('&', '&amp;')
        # 이미 변환된 태그는 복원
        text = text.replace('&amp;lt;', '&lt;').replace('&amp;gt;', '&gt;')
        
        return text
    
    # ============================================================
    # Utility Methods
    # ============================================================
    
    def _extract_llm_response(self, response) -> Optional[str]:
        """Gemini 응답에서 텍스트를 안전하게 추출합니다."""
        try:
            # response.text 시도
            try:
                text = getattr(response, "text", None)
                if text and text.strip():
                    return text.strip()
            except Exception:
                pass
            
            # Fallback: candidates에서 추출
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return None
            
            candidate = candidates[0]
            content_parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            
            extracted_chunks = []
            for part in content_parts:
                text_chunk = getattr(part, "text", None)
                if text_chunk:
                    extracted_chunks.append(text_chunk)
            
            if extracted_chunks:
                return "\n".join(extracted_chunks).strip()
            
            return None
            
        except Exception as e:
            logger.error(f"ReportAgent: LLM 응답 추출 오류: {e}")
            return None

