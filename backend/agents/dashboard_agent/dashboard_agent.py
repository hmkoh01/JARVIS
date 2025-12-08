"""
DashboardAgent - AI 기반 데이터 분석 에이전트

사용자의 자연어 질문을 분석하여:
1. 관심사 트렌드 분석
2. 활동 패턴 분석
3. 기간별 비교 분석
4. 카테고리별 분석
5. 추천 히스토리 분석
6. 사용자 정의 분석

을 수행하고 시각화(차트)와 함께 인사이트를 제공합니다.

채팅에서 직접 호출 시:
1. 사용자 입력에서 분석 유형 추출
2. 확인 메시지 반환 ("~를 분석해드릴까요?")
3. 프론트엔드가 확인 후 /dashboard/analyses/create API 호출
4. 분석 완료 후 대시보드에서 확인 가능

사용 모델: Gemini 2.5 Pro
저장: SQLite dashboard_analyses 테이블
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import google.generativeai as genai

from ..base_agent import BaseAgent, AgentResponse
from config.settings import settings
from database.sqlite import SQLite

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """분석 결과를 나타내는 데이터 클래스"""
    success: bool
    analysis_type: str = ""
    title: str = ""
    content: str = ""
    chart_data: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    message: str = ""


class DashboardAgent(BaseAgent):
    """
    AI 기반 데이터 분석 에이전트
    
    지원 분석 유형:
        - interest_trend: 관심사 트렌드 분석
        - activity_pattern: 활동 패턴 분석
        - period_comparison: 기간별 비교 분석
        - category_analysis: 카테고리별 분석
        - recommendation_stats: 추천 히스토리 분석
        - custom: 사용자 정의 분석
    
    채팅 연동:
        - Supervisor에서 "분석", "트렌드", "통계" 등의 키워드 감지 시 호출
        - 분석 유형 파악 후 확인 메시지 반환
        - 프론트엔드에서 확인 후 /dashboard/analyses/create API로 실제 분석
    """
    
    # 분석 유형 정의
    ANALYSIS_TYPES = {
        "interest_trend": {
            "name": "관심사 트렌드 분석",
            "description": "시간에 따른 관심사 변화와 트렌드를 분석합니다.",
            "keywords": ["관심사", "트렌드", "변화", "추이", "키워드"]
        },
        "activity_pattern": {
            "name": "활동 패턴 분석",
            "description": "채팅, 웹 방문, 파일 작업 등의 활동 패턴을 분석합니다.",
            "keywords": ["활동", "패턴", "사용량", "얼마나", "자주"]
        },
        "period_comparison": {
            "name": "기간별 비교 분석",
            "description": "특정 기간 간의 활동과 관심사를 비교 분석합니다.",
            "keywords": ["비교", "vs", "대비", "저번", "이번", "전주", "전월"]
        },
        "category_analysis": {
            "name": "카테고리별 분석",
            "description": "특정 주제나 카테고리에 대한 심층 분석을 수행합니다.",
            "keywords": ["카테고리", "분야", "주제", "관련", "에 대한"]
        },
        "recommendation_stats": {
            "name": "추천 히스토리 분석",
            "description": "추천 수락률, 선호 주제 등을 분석합니다.",
            "keywords": ["추천", "수락", "거절", "선호", "제안"]
        },
        "custom": {
            "name": "종합 분석",
            "description": "요청에 맞는 맞춤형 종합 분석을 수행합니다.",
            "keywords": []
        }
    }
    
    def __init__(self):
        super().__init__(
            agent_type="dashboard",
            description="데이터 분석, 시각화, 트렌드, 통계 관련 질문을 처리합니다. '내 활동 분석해줘', '관심사 트렌드 보여줘'와 같이 요청할 수 있습니다."
        )
        self.sqlite = SQLite()
        self._init_llm()
    
    def _init_llm(self):
        """Gemini LLM 클라이언트 초기화"""
        self.llm_available = False
        
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY가 설정되지 않아 DashboardAgent LLM 기능을 사용할 수 없습니다.")
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
            
            # JSON 출력용 모델 (분석 유형 파악 등)
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
            
            # 텍스트 출력용 모델 (분석 결과 생성)
            self.llm_model_text = genai.GenerativeModel(
                model_name="gemini-2.5-pro",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 4096,
                    "response_mime_type": "text/plain",
                },
                safety_settings=self.safety_settings,
            )
            
            self.llm_available = True
            logger.info("DashboardAgent: Gemini LLM 클라이언트 초기화 완료")
            
        except Exception as e:
            logger.error(f"DashboardAgent: Gemini LLM 초기화 오류: {e}")
    
    # ============================================================
    # BaseAgent Interface Implementation
    # ============================================================
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        동기 처리 메서드 (langgraph 호환)
        
        채팅에서 분석 요청 시:
        1. 사용자 입력에서 분석 유형 추출
        2. 확인 메시지 반환 (프론트엔드가 확인 후 /dashboard/analyses/create 호출)
        """
        question = state.get("question", "")
        user_id = state.get("user_id")
        
        if not question:
            return {
                **state,
                "answer": "분석할 내용이 제공되지 않았습니다.",
                "success": False,
                "agent_type": self.agent_type
            }
        
        # 비동기 함수를 동기적으로 실행
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._process_analysis_request(user_id, question)
                    )
                    result = future.result(timeout=60)
            else:
                result = loop.run_until_complete(
                    self._process_analysis_request(user_id, question)
                )
        except Exception as e:
            logger.exception(f"DashboardAgent process 오류: {e}")
            result = {
                "success": False,
                "answer": f"분석 요청 처리 중 오류가 발생했습니다: {str(e)}",
                "metadata": {}
            }
        
        return {
            **state,
            "answer": result.get("answer", "분석 요청을 처리할 수 없습니다."),
            "success": result.get("success", False),
            "agent_type": self.agent_type,
            "metadata": result.get("metadata", {})
        }
    
    async def _process_analysis_request(self, user_id: Optional[int], question: str) -> Dict[str, Any]:
        """
        분석 요청을 처리합니다.
        
        1. 사용자 입력에서 분석 유형 추출
        2. 분석 계획 수립
        3. 확인 메시지 반환
        """
        if not self.llm_available:
            return {
                "success": False,
                "answer": "LLM 서비스를 사용할 수 없어 분석 요청을 처리할 수 없습니다.",
                "metadata": {}
            }
        
        # 분석 유형 및 계획 추출
        analysis_plan = await self._extract_analysis_plan(question, user_id)
        
        if not analysis_plan or analysis_plan.get("analysis_type") == "unknown":
            return {
                "success": True,
                "answer": "어떤 분석을 원하시나요? 예를 들어:\n\n"
                         "• 관심사 트렌드 분석\n"
                         "• 활동 패턴 분석\n"
                         "• 기간별 비교 분석\n"
                         "• 추천 히스토리 분석\n\n"
                         "구체적으로 말씀해 주세요!",
                "metadata": {
                    "action": "request_clarification",
                    "message": "분석 유형이 명확하지 않습니다."
                }
            }
        
        analysis_type = analysis_plan.get("analysis_type", "custom")
        analysis_title = analysis_plan.get("title", "데이터 분석")
        analysis_description = analysis_plan.get("description", "")
        analysis_items = analysis_plan.get("analysis_items", [])
        
        # 분석 유형 정보 가져오기
        type_info = self.ANALYSIS_TYPES.get(analysis_type, self.ANALYSIS_TYPES["custom"])
        
        # 확인 메시지 생성
        confirm_message = f"**{analysis_title}**을(를) 원하시는군요!\n\n"
        confirm_message += f"{analysis_description}\n\n"
        confirm_message += "📊 **분석 항목:**\n"
        for item in analysis_items[:5]:
            confirm_message += f"• {item}\n"
        confirm_message += "\n분석에는 약 30초~1분 정도 소요됩니다.\n"
        confirm_message += "분석을 진행할까요?"
        
        return {
            "success": True,
            "answer": confirm_message,
            "metadata": {
                "action": "confirm_analysis",
                "analysis_type": analysis_type,
                "title": analysis_title,
                "description": analysis_description,
                "analysis_items": analysis_items,
                "query": question,
                "requires_confirmation": True
            }
        }
    
    async def _extract_analysis_plan(self, user_input: str, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """
        사용자 입력에서 분석 유형과 계획을 추출합니다.
        """
        # 분석 유형 설명 생성
        types_description = "\n".join([
            f"- {key}: {info['name']} - {info['description']}"
            for key, info in self.ANALYSIS_TYPES.items()
        ])
        
        prompt = f"""당신은 사용자의 데이터 분석 요청을 분석하는 전문가입니다.

## 사용자 요청
"{user_input}"

## 지원하는 분석 유형
{types_description}

## 작업
1. 사용자 요청에서 적절한 분석 유형을 선택하세요.
2. 분석 제목과 설명을 작성하세요.
3. 구체적인 분석 항목 목록을 생성하세요.
4. 분석 유형을 판단할 수 없으면 "unknown"을 반환하세요.

## 출력 형식 (JSON)
{{
    "analysis_type": "분석 유형 (interest_trend, activity_pattern, period_comparison, category_analysis, recommendation_stats, custom, unknown 중 하나)",
    "title": "분석 제목 (한국어, 친근한 톤)",
    "description": "분석 내용 설명 (1-2문장)",
    "analysis_items": ["분석 항목1", "분석 항목2", "분석 항목3", "분석 항목4", "분석 항목5"],
    "confidence": 0.0~1.0,
    "reasoning": "분석 유형 선택 근거"
}}
"""
        
        try:
            response = self.llm_model_json.generate_content(
                prompt,
                request_options={"timeout": 30}
            )
            
            response_text = self._extract_llm_response(response)
            if not response_text:
                return None
            
            result = json.loads(response_text)
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"DashboardAgent: 분석 계획 JSON 파싱 오류: {e}")
            return None
        except Exception as e:
            logger.error(f"DashboardAgent: 분석 계획 추출 오류: {e}")
            return None
    
    async def process_async(self, user_input: str, user_id: Optional[int] = None) -> AgentResponse:
        """비동기 처리 메서드"""
        result = await self._process_analysis_request(user_id, user_input)
        
        return AgentResponse(
            success=result.get("success", False),
            content=result.get("answer", ""),
            agent_type=self.agent_type,
            metadata=result.get("metadata", {})
        )
    
    # ============================================================
    # Main Analysis Method
    # ============================================================
    
    async def create_analysis(
        self,
        user_id: int,
        analysis_type: str,
        query: str
    ) -> Dict[str, Any]:
        """
        분석 실행 메인 메서드
        
        Args:
            user_id: 사용자 ID
            analysis_type: 분석 유형
            query: 원본 질문
        
        Returns:
            {
                "success": bool,
                "analysis_id": int,
                "title": str,
                "content": str,
                "chart_data": dict,
                "insights": list,
                "message": str
            }
        """
        logger.info(f"DashboardAgent: 분석 시작 - type='{analysis_type}', user_id={user_id}")
        
        try:
            if not self.llm_available:
                return {
                    "success": False,
                    "message": "LLM 서비스를 사용할 수 없습니다."
                }
            
            # ==============================
            # Step 1: 데이터 수집
            # ==============================
            logger.info("DashboardAgent: Step 1 - 데이터 수집")
            raw_data = await self._collect_data(user_id, analysis_type)
            
            if not raw_data:
                return {
                    "success": False,
                    "message": "분석할 데이터가 충분하지 않습니다."
                }
            
            # ==============================
            # Step 2: 데이터 분석 및 인사이트 생성
            # ==============================
            logger.info("DashboardAgent: Step 2 - 데이터 분석")
            analysis_result = await self._analyze_data(user_id, analysis_type, query, raw_data)
            
            if not analysis_result:
                return {
                    "success": False,
                    "message": "분석 결과 생성에 실패했습니다."
                }
            
            # ==============================
            # Step 3: 차트 생성 (여러 개)
            # ==============================
            logger.info("DashboardAgent: Step 3 - 차트 생성")
            charts = await self._generate_charts(analysis_type, raw_data, analysis_result)
            
            # ==============================
            # Step 4: DB 저장
            # ==============================
            logger.info("DashboardAgent: Step 4 - DB 저장")
            # chart_data는 이제 리스트 형태로 저장 ({"charts": [...]})
            chart_data = {"charts": charts} if charts else None
            analysis_id = self.sqlite.create_analysis(
                user_id=user_id,
                analysis_type=analysis_type,
                title=analysis_result.get("title", "데이터 분석"),
                content=analysis_result.get("content", ""),
                chart_data=chart_data,
                insights=analysis_result.get("insights", []),
                query=query
            )
            
            if not analysis_id:
                return {
                    "success": False,
                    "message": "분석 결과 저장에 실패했습니다."
                }
            
            logger.info(f"DashboardAgent: 분석 완료 - analysis_id={analysis_id}")
            
            return {
                "success": True,
                "analysis_id": analysis_id,
                "title": analysis_result.get("title", ""),
                "content": analysis_result.get("content", ""),
                "chart_data": chart_data,  # {"charts": [...]} 형태
                "charts": charts,  # 편의를 위한 직접 리스트
                "insights": analysis_result.get("insights", []),
                "message": ""
            }
            
        except Exception as e:
            logger.exception(f"DashboardAgent: 분석 중 오류: {e}")
            return {
                "success": False,
                "message": f"분석 중 오류가 발생했습니다: {str(e)}"
            }
    
    # ============================================================
    # Data Collection
    # ============================================================
    
    async def _collect_data(self, user_id: int, analysis_type: str) -> Dict[str, Any]:
        """분석에 필요한 데이터를 수집합니다."""
        data = {}
        
        try:
            # 공통 데이터
            data["interest_summary"] = self.sqlite.get_interest_summary(user_id)
            data["activity_summary"] = self.sqlite.get_activity_summary(user_id)
            data["interests"] = self.sqlite.get_user_interests(user_id, limit=50)
            
            # 분석 유형별 추가 데이터
            if analysis_type == "interest_trend":
                data["interest_trend"] = self.sqlite.get_interest_trend(user_id, days=30)
                data["keyword_frequency"] = self.sqlite.get_keyword_frequency(user_id, limit=30)
                
            elif analysis_type == "activity_pattern":
                data["chat_messages"] = self.sqlite.get_recent_chat_messages(user_id, limit=100)
                data["browser_logs"] = self.sqlite.get_browser_logs(user_id, limit=100)
                data["activity_7d"] = self.sqlite.get_activity_summary(user_id, days=7)
                data["activity_30d"] = self.sqlite.get_activity_summary(user_id, days=30)
                
            elif analysis_type == "period_comparison":
                data["activity_7d"] = self.sqlite.get_activity_summary(user_id, days=7)
                data["activity_14d"] = self.sqlite.get_activity_summary(user_id, days=14)
                data["activity_30d"] = self.sqlite.get_activity_summary(user_id, days=30)
                data["interest_trend"] = self.sqlite.get_interest_trend(user_id, days=30)
                
            elif analysis_type == "recommendation_stats":
                data["recommendations"] = self.sqlite.get_all_recommendations(user_id, limit=100)
                
            elif analysis_type == "category_analysis":
                data["keyword_frequency"] = self.sqlite.get_keyword_frequency(user_id, limit=50)
                data["content_keywords"] = self.sqlite.get_content_keywords(user_id, limit=100)
            
            # custom 또는 기타: 모든 데이터 수집
            else:
                data["interest_trend"] = self.sqlite.get_interest_trend(user_id, days=30)
                data["keyword_frequency"] = self.sqlite.get_keyword_frequency(user_id, limit=30)
                data["recommendations"] = self.sqlite.get_all_recommendations(user_id, limit=50)
                data["chat_messages"] = self.sqlite.get_recent_chat_messages(user_id, limit=50)
            
            return data
            
        except Exception as e:
            logger.error(f"DashboardAgent: 데이터 수집 오류: {e}")
            return {}
    
    # ============================================================
    # Data Analysis
    # ============================================================
    
    async def _analyze_data(
        self,
        user_id: int,
        analysis_type: str,
        query: str,
        raw_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """LLM을 사용하여 데이터를 분석하고 인사이트를 생성합니다."""
        
        # 데이터 요약 생성
        data_summary = self._create_data_summary(raw_data)
        
        type_info = self.ANALYSIS_TYPES.get(analysis_type, self.ANALYSIS_TYPES["custom"])
        
        prompt = f"""당신은 사용자 데이터를 분석하는 전문 데이터 분석가입니다.
친근하고 이해하기 쉬운 말투로 분석 결과를 작성해주세요.

## 분석 유형
{type_info['name']}: {type_info['description']}

## 사용자 원본 질문
"{query}"

## 수집된 데이터
{data_summary}

## 작업
1. 데이터를 심층 분석하세요.
2. 주요 발견사항과 패턴을 파악하세요.
3. 실행 가능한 인사이트를 도출하세요.
4. 친근하고 이해하기 쉬운 분석 보고서를 작성하세요.

## 분석 보고서 형식
다음 구조로 Markdown 형식의 보고서를 작성하세요:

### 📊 분석 요약
(전체 분석 결과를 2-3문장으로 요약)

### 🔍 주요 발견사항
(데이터에서 발견된 주요 패턴이나 특징을 불릿 포인트로 정리)

### 💡 인사이트
(분석을 통해 도출된 인사이트와 의미 해석)

### 🎯 추천 액션
(사용자에게 제안하는 다음 행동이나 개선점)

## 출력
Markdown 형식의 분석 보고서를 작성하세요.
"""

        try:
            response = self.llm_model_text.generate_content(
                prompt,
                request_options={"timeout": 120}
            )
            
            content = self._extract_llm_response(response)
            
            if not content:
                return None
            
            # 인사이트 추출
            insights = await self._extract_insights(content, query)
            
            # 제목 생성
            title = await self._generate_title(analysis_type, query)
            
            return {
                "title": title,
                "content": content,
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"DashboardAgent: 데이터 분석 오류: {e}")
            return None
    
    def _create_data_summary(self, raw_data: Dict[str, Any]) -> str:
        """수집된 데이터를 LLM에 전달할 형태로 요약합니다."""
        summary_parts = []
        
        # 관심사 요약
        if "interest_summary" in raw_data:
            interest = raw_data["interest_summary"]
            summary_parts.append(f"""
### 관심사 요약
- 총 관심사 수: {interest.get('total_count', 0)}개
- 상위 관심사: {', '.join([i['keyword'] for i in interest.get('top_interests', [])[:5]])}
- 최근 추가된 관심사: {', '.join([i['keyword'] for i in interest.get('recent_interests', [])[:3]])}
""")
        
        # 활동 요약
        if "activity_summary" in raw_data:
            activity = raw_data["activity_summary"]
            summary_parts.append(f"""
### 활동 요약 (최근 {activity.get('period_days', 7)}일)
- 채팅 메시지: {activity.get('chat_messages', 0)}건
- 웹 방문: {activity.get('browser_visits', 0)}건
- 파일 처리: {activity.get('files_processed', 0)}건
- 추천: 총 {activity.get('recommendations', {}).get('total', 0)}건 (수락 {activity.get('recommendations', {}).get('accepted', 0)} / 거절 {activity.get('recommendations', {}).get('rejected', 0)})
""")
        
        # 관심사 트렌드
        if "interest_trend" in raw_data and raw_data["interest_trend"]:
            trend_data = raw_data["interest_trend"][:20]  # 최근 20개만
            if trend_data:
                summary_parts.append(f"""
### 관심사 트렌드 (최근 데이터)
{json.dumps(trend_data, ensure_ascii=False, indent=2)[:1500]}
""")
        
        # 키워드 빈도
        if "keyword_frequency" in raw_data and raw_data["keyword_frequency"]:
            freq_data = raw_data["keyword_frequency"][:15]
            summary_parts.append(f"""
### 키워드 빈도 (상위 15개)
{json.dumps(freq_data, ensure_ascii=False, indent=2)}
""")
        
        # 추천 통계
        if "recommendations" in raw_data and raw_data["recommendations"]:
            recs = raw_data["recommendations"]
            accepted = sum(1 for r in recs if r.get('status') == 'accepted')
            rejected = sum(1 for r in recs if r.get('status') == 'rejected')
            pending = sum(1 for r in recs if r.get('status') == 'pending')
            keywords = [r.get('keyword', '') for r in recs[:10] if r.get('keyword')]
            summary_parts.append(f"""
### 추천 히스토리
- 총 추천 수: {len(recs)}
- 수락: {accepted}, 거절: {rejected}, 대기중: {pending}
- 최근 추천 키워드: {', '.join(keywords)}
""")
        
        # 기간별 비교
        if "activity_7d" in raw_data and "activity_30d" in raw_data:
            a7 = raw_data["activity_7d"]
            a30 = raw_data["activity_30d"]
            summary_parts.append(f"""
### 기간별 활동 비교
- 7일간 채팅: {a7.get('chat_messages', 0)}건 / 30일간: {a30.get('chat_messages', 0)}건
- 7일간 웹 방문: {a7.get('browser_visits', 0)}건 / 30일간: {a30.get('browser_visits', 0)}건
""")
        
        return "\n".join(summary_parts) if summary_parts else "수집된 데이터가 없습니다."
    
    async def _extract_insights(self, content: str, query: str) -> List[str]:
        """분석 내용에서 핵심 인사이트를 추출합니다."""
        prompt = f"""다음 분석 내용에서 핵심 인사이트 3개를 추출하세요.
각 인사이트는 한 문장으로 간결하게 작성하세요.

분석 내용:
{content[:1500]}

반드시 JSON 배열 형식으로만 출력하세요 (다른 텍스트 없이):
["첫 번째 인사이트", "두 번째 인사이트", "세 번째 인사이트"]
"""
        
        try:
            response = self.llm_model_json.generate_content(
                prompt,
                request_options={"timeout": 30}
            )
            
            response_text = self._extract_llm_response(response)
            if response_text:
                # JSON 배열 추출 시도
                import re
                # JSON 배열 패턴 찾기
                array_match = re.search(r'\[.*?\]', response_text, re.DOTALL)
                if array_match:
                    json_str = array_match.group()
                    # 줄바꿈을 공백으로 변환하여 파싱
                    json_str = json_str.replace('\n', ' ').replace('\r', '')
                    return json.loads(json_str)
                # 전체 텍스트가 JSON 배열인 경우
                return json.loads(response_text)
            return self._fallback_extract_insights(content)
        except json.JSONDecodeError as e:
            logger.warning(f"DashboardAgent: 인사이트 JSON 파싱 실패, 폴백 사용: {e}")
            return self._fallback_extract_insights(content)
        except Exception as e:
            logger.error(f"DashboardAgent: 인사이트 추출 오류: {e}")
            return self._fallback_extract_insights(content)
    
    def _fallback_extract_insights(self, content: str) -> List[str]:
        """인사이트 추출 실패 시 폴백: 분석 내용에서 주요 포인트 추출"""
        insights = []
        
        # "인사이트" 섹션 찾기
        if "💡 인사이트" in content or "### 💡" in content:
            lines = content.split('\n')
            in_insight_section = False
            for line in lines:
                if "💡" in line and ("인사이트" in line or "Insight" in line):
                    in_insight_section = True
                    continue
                if in_insight_section:
                    if line.startswith('#') or line.startswith('🎯'):
                        break
                    line = line.strip()
                    if line.startswith('-') or line.startswith('•'):
                        insight = line.lstrip('-•').strip()
                        insight = insight.replace('**', '').strip()
                        if insight and len(insight) > 10:
                            insights.append(insight)
                            if len(insights) >= 3:
                                break
        
        # 찾지 못한 경우 요약 섹션에서 추출
        if not insights:
            if "📊 분석 요약" in content or "### 📊" in content:
                lines = content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#') and len(line) > 20:
                        line = line.replace('**', '').strip()
                        if not line.startswith('-') and not line.startswith('•'):
                            insights.append(line[:100])
                            break
        
        return insights if insights else ["분석 결과를 확인해주세요."]
    
    async def _generate_title(self, analysis_type: str, query: str) -> str:
        """분석 제목을 생성합니다."""
        type_info = self.ANALYSIS_TYPES.get(analysis_type, self.ANALYSIS_TYPES["custom"])
        
        prompt = f"""다음 분석 요청에 대한 짧고 명확한 제목을 생성하세요.

분석 유형: {type_info['name']}
사용자 요청: {query}

제목 규칙:
- 15자 이내
- 한국어로 작성
- 이모지 포함 가능

JSON 형식으로 출력:
{{"title": "생성된 제목"}}
"""
        
        try:
            response = self.llm_model_json.generate_content(
                prompt,
                request_options={"timeout": 15}
            )
            
            response_text = self._extract_llm_response(response)
            if response_text:
                result = json.loads(response_text)
                return result.get("title", type_info['name'])
            return type_info['name']
        except Exception:
            return type_info['name']
    
    # ============================================================
    # Chart Generation (Multiple Charts)
    # ============================================================
    
    async def _generate_charts(
        self,
        analysis_type: str,
        raw_data: Dict[str, Any],
        analysis_result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Plotly를 사용하여 여러 차트를 생성합니다.
        
        분석 유형과 가용 데이터에 따라 적절한 차트들을 생성합니다.
        """
        charts = []
        
        try:
            if analysis_type == "interest_trend":
                # 관심사 트렌드: 관심사 TOP 10 + 키워드 빈도 차트
                chart = self._create_interest_trend_chart(raw_data)
                if chart and chart.get("type") != "empty":
                    charts.append(chart)
                
                # 키워드 빈도 차트 추가
                if raw_data.get("keyword_frequency"):
                    keyword_chart = self._create_category_chart(raw_data)
                    if keyword_chart and keyword_chart.get("type") != "empty":
                        charts.append(keyword_chart)
                        
            elif analysis_type == "activity_pattern":
                # 활동 패턴: 활동 현황 + 기간 비교 (가능한 경우)
                chart = self._create_activity_chart(raw_data)
                if chart and chart.get("type") != "empty":
                    charts.append(chart)
                
                # 7일 vs 30일 비교 가능하면 추가
                if raw_data.get("activity_7d") and raw_data.get("activity_30d"):
                    comparison_chart = self._create_comparison_chart(raw_data)
                    if comparison_chart and comparison_chart.get("type") != "empty":
                        charts.append(comparison_chart)
                        
            elif analysis_type == "period_comparison":
                # 기간별 비교: 비교 차트 + 활동 차트
                chart = self._create_comparison_chart(raw_data)
                if chart and chart.get("type") != "empty":
                    charts.append(chart)
                
                # 관심사 트렌드 추가
                if raw_data.get("interests"):
                    trend_chart = self._create_interest_trend_chart(raw_data)
                    if trend_chart and trend_chart.get("type") != "empty":
                        charts.append(trend_chart)
                        
            elif analysis_type == "recommendation_stats":
                # 추천 통계: 파이 차트 + 관심사 차트
                chart = self._create_recommendation_chart(raw_data)
                if chart and chart.get("type") != "empty":
                    charts.append(chart)
                
                # 관심사 데이터가 있으면 추가
                if raw_data.get("interests"):
                    interest_chart = self._create_interest_trend_chart(raw_data)
                    if interest_chart and interest_chart.get("type") != "empty":
                        charts.append(interest_chart)
                        
            elif analysis_type == "category_analysis":
                # 카테고리 분석: 키워드 빈도 + 관심사
                chart = self._create_category_chart(raw_data)
                if chart and chart.get("type") != "empty":
                    charts.append(chart)
                
                if raw_data.get("interests"):
                    interest_chart = self._create_interest_trend_chart(raw_data)
                    if interest_chart and interest_chart.get("type") != "empty":
                        charts.append(interest_chart)
                        
            else:
                # custom: 모든 가용 데이터에 대해 차트 생성
                charts = self._create_all_available_charts(raw_data)
            
            return charts
            
        except Exception as e:
            logger.error(f"DashboardAgent: 차트 생성 오류: {e}")
            return []
    
    def _create_all_available_charts(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """가용한 모든 데이터에 대해 차트를 생성합니다."""
        charts = []
        
        # 관심사 데이터
        if raw_data.get("interests"):
            chart = self._create_interest_trend_chart(raw_data)
            if chart and chart.get("type") != "empty":
                charts.append(chart)
        
        # 활동 데이터
        if raw_data.get("activity_summary"):
            chart = self._create_activity_chart(raw_data)
            if chart and chart.get("type") != "empty":
                charts.append(chart)
        
        # 기간별 비교
        if raw_data.get("activity_7d") and raw_data.get("activity_30d"):
            chart = self._create_comparison_chart(raw_data)
            if chart and chart.get("type") != "empty":
                charts.append(chart)
        
        # 추천 데이터
        if raw_data.get("recommendations"):
            chart = self._create_recommendation_chart(raw_data)
            if chart and chart.get("type") != "empty":
                charts.append(chart)
        
        # 키워드 빈도
        if raw_data.get("keyword_frequency"):
            chart = self._create_category_chart(raw_data)
            if chart and chart.get("type") != "empty":
                charts.append(chart)
        
        return charts
    
    def _create_interest_trend_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """관심사 트렌드 차트 생성"""
        interests = raw_data.get("interests", [])[:10]
        
        if not interests:
            return {"type": "empty", "message": "관심사 데이터가 없습니다."}
        
        keywords = [i.get("keyword", "")[:15] for i in interests]
        scores = [i.get("score", 0) for i in interests]
        
        fig = go.Figure(data=[
            go.Bar(
                x=scores,
                y=keywords,
                orientation='h',
                marker=dict(
                    color=scores,
                    colorscale='Blues',
                    showscale=False
                ),
                text=[f'{s:.2f}' for s in scores],
                textposition='outside'
            )
        ])
        
        fig.update_layout(
            title="관심사 TOP 10",
            xaxis_title="관심도 점수",
            yaxis_title="",
            height=400,
            margin=dict(l=120, r=40, t=60, b=40),
            yaxis=dict(autorange="reversed"),
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        return {
            "type": "bar",
            "plotly_json": fig.to_json(),
            "title": "관심사 TOP 10"
        }
    
    def _create_activity_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """활동 패턴 차트 생성"""
        activity = raw_data.get("activity_summary", {})
        
        categories = ['채팅', '웹 방문', '파일 처리']
        values = [
            activity.get('chat_messages', 0),
            activity.get('browser_visits', 0),
            activity.get('files_processed', 0)
        ]
        
        fig = go.Figure(data=[
            go.Bar(
                x=categories,
                y=values,
                marker_color=['#3B82F6', '#10B981', '#F59E0B'],
                text=values,
                textposition='outside'
            )
        ])
        
        fig.update_layout(
            title=f"최근 {activity.get('period_days', 7)}일 활동",
            xaxis_title="활동 유형",
            yaxis_title="건수",
            height=350,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        return {
            "type": "bar",
            "plotly_json": fig.to_json(),
            "title": "활동 현황"
        }
    
    def _create_comparison_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """기간별 비교 차트 생성"""
        a7 = raw_data.get("activity_7d", {})
        a30 = raw_data.get("activity_30d", {})
        
        categories = ['채팅', '웹 방문', '파일 처리']
        values_7d = [
            a7.get('chat_messages', 0),
            a7.get('browser_visits', 0),
            a7.get('files_processed', 0)
        ]
        values_30d = [
            a30.get('chat_messages', 0),
            a30.get('browser_visits', 0),
            a30.get('files_processed', 0)
        ]
        
        fig = go.Figure(data=[
            go.Bar(name='최근 7일', x=categories, y=values_7d, marker_color='#3B82F6'),
            go.Bar(name='최근 30일', x=categories, y=values_30d, marker_color='#93C5FD')
        ])
        
        fig.update_layout(
            title="기간별 활동 비교",
            xaxis_title="활동 유형",
            yaxis_title="건수",
            barmode='group',
            height=350,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        return {
            "type": "grouped_bar",
            "plotly_json": fig.to_json(),
            "title": "기간별 비교"
        }
    
    def _create_recommendation_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """추천 통계 차트 생성"""
        recs = raw_data.get("recommendations", [])
        
        if not recs:
            return {"type": "empty", "message": "추천 데이터가 없습니다."}
        
        accepted = sum(1 for r in recs if r.get('status') == 'accepted')
        rejected = sum(1 for r in recs if r.get('status') == 'rejected')
        pending = sum(1 for r in recs if r.get('status') == 'pending')
        shown = sum(1 for r in recs if r.get('status') == 'shown')
        
        labels = ['수락', '거절', '대기중', '표시됨']
        values = [accepted, rejected, pending, shown]
        colors = ['#10B981', '#EF4444', '#F59E0B', '#6B7280']
        
        fig = go.Figure(data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.4,
                marker_colors=colors,
                textinfo='label+percent',
                textposition='outside'
            )
        ])
        
        fig.update_layout(
            title="추천 응답 현황",
            height=350,
            showlegend=True,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        return {
            "type": "pie",
            "plotly_json": fig.to_json(),
            "title": "추천 통계"
        }
    
    def _create_category_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """카테고리별 차트 생성"""
        keyword_freq = raw_data.get("keyword_frequency", [])[:10]
        
        if not keyword_freq:
            return {"type": "empty", "message": "키워드 데이터가 없습니다."}
        
        keywords = [k.get("keyword", "")[:12] for k in keyword_freq]
        counts = [k.get("count", 0) for k in keyword_freq]
        
        fig = go.Figure(data=[
            go.Bar(
                x=keywords,
                y=counts,
                marker_color='#8B5CF6',
                text=counts,
                textposition='outside'
            )
        ])
        
        fig.update_layout(
            title="키워드 빈도 TOP 10",
            xaxis_title="키워드",
            yaxis_title="빈도",
            height=350,
            xaxis_tickangle=-45,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        
        return {
            "type": "bar",
            "plotly_json": fig.to_json(),
            "title": "키워드 빈도"
        }
    
    def _create_combined_chart(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """종합 차트 생성 (가장 적합한 차트 자동 선택) - 하위 호환성용"""
        # 관심사 데이터가 있으면 관심사 차트
        if raw_data.get("interests"):
            return self._create_interest_trend_chart(raw_data)
        # 활동 데이터가 있으면 활동 차트
        elif raw_data.get("activity_summary"):
            return self._create_activity_chart(raw_data)
        # 추천 데이터가 있으면 추천 차트
        elif raw_data.get("recommendations"):
            return self._create_recommendation_chart(raw_data)
        else:
            return {"type": "empty", "message": "시각화할 데이터가 없습니다."}
    
    # ============================================================
    # Utility Methods
    # ============================================================
    
    def _extract_llm_response(self, response) -> Optional[str]:
        """Gemini 응답에서 텍스트를 안전하게 추출합니다."""
        try:
            try:
                text = getattr(response, "text", None)
                if text and text.strip():
                    return text.strip()
            except Exception:
                pass
            
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
            logger.error(f"DashboardAgent: LLM 응답 추출 오류: {e}")
            return None
