"""
RecommendationAgent - 능동형 추천 에이전트 (Active Agent)

LLM(Gemini)이 사용자의 로그와 관심사(Survey)를 분석하여 적절한 타이밍에
"말풍선 메시지"를 제안하는 방식으로 동작합니다.
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple

import google.generativeai as genai

from ..base_agent import BaseAgent, AgentResponse
from database.sqlite import SQLite
from config.settings import settings

logger = logging.getLogger(__name__)


class RecommendationAgent(BaseAgent):
    """능동형 추천 에이전트 - LLM 기반 분석 및 말풍선 메시지 생성"""
    
    def __init__(self):
        super().__init__(
            agent_type="recommendation",
            description="사용자 활동을 분석하여 맞춤형 추천을 제공합니다."
        )
        self.sqlite = SQLite()
        self._init_llm()
    
    def _init_llm(self):
        """Gemini LLM 클라이언트 초기화"""
        self.llm_available = False
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY가 설정되지 않아 LLM 기능을 사용할 수 없습니다.")
            return
        
        try:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self.safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            self.llm_model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_output_tokens": 1024,
                    "response_mime_type": "application/json",
                },
                safety_settings=self.safety_settings,
            )
            self.llm_available = True
            logger.info("Gemini LLM 클라이언트 초기화 완료")
        except Exception as e:
            logger.error(f"Gemini LLM 초기화 오류: {e}")
    
    # ============================================================
    # BaseAgent Interface Implementation
    # ============================================================
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """상태를 받아서 처리하고 수정된 상태를 반환합니다."""
        question = state.get("question", "")
        user_id = state.get("user_id")
        
        if not question:
            return {**state, "answer": "질문이 제공되지 않았습니다.", "evidence": []}
        
        try:
            # 사용자에게 대기 중인 추천이 있는지 확인
            pending = self.get_pending_recommendations(user_id) if user_id else []
            
            if pending:
                response_content = (
                    f"현재 {len(pending)}개의 추천이 대기 중입니다.\n"
                    f"첫 번째 추천: {pending[0].get('bubble_message', '새로운 추천이 있어요!')}"
                )
            else:
                response_content = "현재 대기 중인 추천이 없습니다. 활동을 계속하시면 맞춤형 추천을 준비해 드릴게요!"
            
            return {
                **state,
                "answer": response_content,
                "evidence": [],
                "agent_type": "recommendation",
                "metadata": {
                    "query": question,
                    "user_id": user_id,
                    "pending_count": len(pending)
                }
            }
        except Exception as e:
            return {
                **state,
                "answer": f"추천 에이전트 처리 중 오류가 발생했습니다: {str(e)}",
                "evidence": [],
                "agent_type": "recommendation"
            }
    
    async def process_async(self, user_input: str, user_id: Optional[int] = None) -> AgentResponse:
        """사용자 입력을 비동기로 처리합니다."""
        try:
            pending = self.get_pending_recommendations(user_id) if user_id else []
            
            if pending:
                content = f"대기 중인 추천이 {len(pending)}개 있습니다."
            else:
                content = "현재 대기 중인 추천이 없습니다."
            
            return AgentResponse(
                success=True,
                content=content,
                agent_type=self.agent_type,
                metadata={"user_id": user_id, "pending_count": len(pending)}
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                content=f"추천 에이전트 처리 중 오류: {str(e)}",
                agent_type=self.agent_type
            )
    
    # ============================================================
    # Core Active Analysis Methods
    # ============================================================
    
    async def run_active_analysis(self, user_id: int) -> Tuple[bool, str]:
        """
        능동형 분석 실행 - 주기적으로 호출되어 추천을 생성합니다.
        
        Returns:
            Tuple[bool, str]: (성공 여부, 메시지)
        """
        logger.info(f"사용자 {user_id}에 대한 능동형 분석 시작...")
        
        if not self.llm_available:
            return False, "LLM 서비스를 사용할 수 없습니다."
        
        try:
            # Step 1: Data Preparation
            browser_logs = self.sqlite.get_unprocessed_browser_logs(user_id)
            app_logs = self.sqlite.get_unprocessed_app_logs(user_id)
            
            if not browser_logs and not app_logs:
                logger.info(f"User {user_id}: 분석할 새로운 로그가 없습니다.")
                return False, "분석할 새로운 활동 데이터가 없습니다."
            
            # 참조 데이터 조회
            blacklist = self.sqlite.get_blacklist(user_id)
            user_interests = self.sqlite.get_user_interests(user_id)
            survey_data = self.sqlite.get_survey_response(user_id)
            
            # Step 2: LLM Analysis & Decision
            analysis_result = await self._analyze_with_llm(
                browser_logs=browser_logs,
                app_logs=app_logs,
                blacklist=blacklist,
                user_interests=user_interests,
                survey_data=survey_data
            )
            
            # Step 3: Process Results
            browser_log_ids = [log['id'] for log in browser_logs]
            app_log_ids = [log['id'] for log in app_logs]
            
            # 로그를 처리됨으로 표시 (추천 생성 여부와 관계없이)
            if browser_log_ids:
                self.sqlite.mark_browser_logs_processed(browser_log_ids)
            if app_log_ids:
                self.sqlite.mark_app_logs_processed(app_log_ids)
            
            if not analysis_result or not analysis_result.get('should_recommend'):
                logger.info(f"User {user_id}: LLM이 추천할 만한 내용이 없다고 판단했습니다.")
                return False, "현재 추천할 만한 특별한 활동이 감지되지 않았습니다."
            
            # 추천 생성
            rec_id = self.sqlite.create_recommendation(
                user_id=user_id,
                trigger_type=analysis_result.get('trigger_type', 'new_interest'),
                keyword=analysis_result.get('keyword', ''),
                bubble_message=analysis_result.get('bubble_message', ''),
                related_keywords=analysis_result.get('related_keywords', [])
            )
            
            if rec_id <= 0:
                logger.error(f"User {user_id}: 추천 저장에 실패했습니다.")
                return False, "추천 저장에 실패했습니다."
            
            # 새로운 관심사라면 등록
            if analysis_result.get('trigger_type') == 'new_interest':
                keyword = analysis_result.get('keyword')
                if keyword:
                    self.sqlite.upsert_interest(
                        user_id=user_id,
                        keyword=keyword,
                        score=0.6,
                        source='active_analysis'
                    )
            
            logger.info(f"✅ User {user_id}: 새로운 추천 생성 완료 (ID: {rec_id})")
            return True, f"새로운 추천이 생성되었습니다: {analysis_result.get('keyword')}"
            
        except Exception as e:
            logger.error(f"User {user_id} 능동형 분석 중 오류: {e}", exc_info=True)
            return False, f"분석 중 오류가 발생했습니다: {e}"
    
    async def _analyze_with_llm(
        self,
        browser_logs: List[Dict[str, Any]],
        app_logs: List[Dict[str, Any]],
        blacklist: List[str],
        user_interests: List[Dict[str, Any]],
        survey_data: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        LLM을 사용하여 로그를 분석하고 추천 여부를 결정합니다.
        
        Returns:
            분석 결과 딕셔너리 또는 None
        """
        # 로그 요약 생성
        log_summary = self._prepare_log_summary(browser_logs, app_logs)
        
        # 기존 관심사 목록
        existing_interests = [item['keyword'] for item in user_interests]
        
        # Survey 정보 추출
        survey_info = ""
        if survey_data:
            job_field = survey_data.get('job_field_other') or survey_data.get('job_field', '')
            interests = survey_data.get('interests', [])
            custom_keywords = survey_data.get('custom_keywords', '')
            survey_info = f"""
설문지 정보:
- 직업/분야: {job_field}
- 관심 분야: {', '.join(interests) if interests else '없음'}
- 커스텀 키워드: {custom_keywords if custom_keywords else '없음'}
"""
        
        prompt = f"""당신은 사용자의 활동을 분석하여 맞춤형 추천을 제안하는 AI 어시스턴트입니다.

## 사용자 활동 로그
{log_summary}

## 기존 관심사
{', '.join(existing_interests) if existing_interests else '등록된 관심사 없음'}

{survey_info}

## 블랙리스트 (추천 제외 키워드)
{', '.join(blacklist) if blacklist else '없음'}

## 분석 지시사항
1. 로그에서 의미 있는 키워드와 주제를 추출하세요.
2. 블랙리스트에 있는 키워드는 절대 추천하지 마세요.
3. 다음 두 가지 케이스 중 하나를 판단하세요:
   - **Case A (new_interest)**: 기존 관심사에 없던 새로운 주제가 발견된 경우
   - **Case B (periodic_expansion)**: 기존 관심사를 더 깊게 탐구하는 활동이 감지된 경우
4. 추천할 만한 내용이 없다면 should_recommend를 false로 설정하세요.
5. 추천 시, 사용자에게 건넬 **친근한 한국어 말풍선 메시지**를 작성하세요.
   - 예시: "요즘 Python에 관심이 많으시네요! 관련 자료를 찾아볼까요? 🐍"

## 출력 형식 (JSON)
{{
    "should_recommend": true/false,
    "trigger_type": "new_interest" 또는 "periodic_expansion",
    "keyword": "핵심 키워드 (한 단어 또는 짧은 구문)",
    "related_keywords": ["관련", "키워드", "목록"],
    "bubble_message": "친근한 한국어 말풍선 메시지",
    "reasoning": "판단 근거 (내부용)"
}}

만약 추천할 내용이 없다면:
{{
    "should_recommend": false,
    "reasoning": "추천하지 않는 이유"
}}
"""

        try:
            response = self.llm_model.generate_content(
                prompt,
                request_options={"timeout": 30}
            )
            
            # 응답 파싱
            result_text = self._extract_llm_response_text(response)
            if not result_text:
                return None
            
            # JSON 파싱
            result = json.loads(result_text)
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"LLM 응답 JSON 파싱 오류: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM 분석 중 오류: {e}", exc_info=True)
            return None
    
    def _prepare_log_summary(
        self,
        browser_logs: List[Dict[str, Any]],
        app_logs: List[Dict[str, Any]]
    ) -> str:
        """로그 데이터를 LLM 프롬프트용 요약 텍스트로 변환합니다."""
        lines = []
        
        if browser_logs:
            lines.append("### 브라우저 방문 기록")
            for log in browser_logs[:30]:  # 최대 30개
                title = log.get('title', '제목 없음')
                url = log.get('url', '')
                # URL에서 도메인 추출
                domain = url.split('/')[2] if url.startswith('http') and len(url.split('/')) > 2 else url
                lines.append(f"- {title} ({domain})")
        
        if app_logs:
            lines.append("\n### 앱 사용 기록")
            for log in app_logs[:20]:  # 최대 20개
                app_name = log.get('app_name', '알 수 없음')
                window_title = log.get('window_title', '')
                duration = log.get('duration_seconds', 0)
                if window_title:
                    lines.append(f"- {app_name}: {window_title} ({duration}초)")
                else:
                    lines.append(f"- {app_name} ({duration}초)")
        
        return '\n'.join(lines) if lines else "활동 로그 없음"
    
    def _extract_llm_response_text(self, response) -> Optional[str]:
        """Gemini 응답에서 텍스트를 안전하게 추출합니다."""
        try:
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
            
            if not extracted_chunks:
                # Fallback: response.text
                try:
                    return (response.text or "").strip()
                except Exception:
                    return None
            
            return "\n".join(extracted_chunks).strip()
            
        except Exception as e:
            logger.error(f"LLM 응답 추출 오류: {e}")
            return None
    
    # ============================================================
    # Interaction Handling
    # ============================================================
    
    async def handle_response(self, recommendation_id: int, action: str) -> Tuple[bool, str]:
        """
        UI에서 사용자가 추천에 응답했을 때 처리합니다.
        
        Args:
            recommendation_id: 추천 ID
            action: 'accept' 또는 'reject'
        
        Returns:
            Tuple[bool, str]: (성공 여부, 결과 메시지 또는 리포트)
        """
        logger.info(f"추천 {recommendation_id}에 대한 응답 처리: {action}")
        
        # 추천 정보 조회
        recommendation = self.sqlite.get_recommendation(recommendation_id)
        if not recommendation:
            return False, "추천을 찾을 수 없습니다."
        
        if action == 'accept':
            return await self._handle_accept(recommendation)
        elif action == 'reject':
            return await self._handle_reject(recommendation)
        else:
            return False, f"알 수 없는 액션: {action}"
    
    async def _handle_accept(self, recommendation: Dict[str, Any]) -> Tuple[bool, str]:
        """추천 수락 처리 - 리포트 생성"""
        rec_id = recommendation['id']
        user_id = recommendation['user_id']
        keyword = recommendation.get('keyword', '')
        related_keywords = recommendation.get('related_keywords', [])
        
        # 상태 업데이트
        self.sqlite.update_recommendation_status(rec_id, 'accepted')
        
        # LLM으로 요약 리포트 생성
        if self.llm_available:
            report_content = await self._generate_report(
                keyword=keyword,
                related_keywords=related_keywords,
                user_id=user_id
            )
        else:
            report_content = f"## {keyword} 관련 정보\n\n관심 키워드: {keyword}\n관련 키워드: {', '.join(related_keywords)}\n\n*LLM 서비스를 사용할 수 없어 상세 리포트를 생성하지 못했습니다.*"
        
        # 리포트 저장
        self.sqlite.update_recommendation_report(rec_id, report_content)
        
        # 관심사 점수 상향 조정
        if keyword:
            self.sqlite.upsert_interest(
                user_id=user_id,
                keyword=keyword,
                score=0.8,
                source='user_accepted'
            )
        
        logger.info(f"✅ 추천 {rec_id} 수락 처리 완료")
        return True, report_content
    
    async def _handle_reject(self, recommendation: Dict[str, Any]) -> Tuple[bool, str]:
        """추천 거절 처리 - 블랙리스트 추가"""
        rec_id = recommendation['id']
        user_id = recommendation['user_id']
        keyword = recommendation.get('keyword', '')
        
        # 상태 업데이트
        self.sqlite.update_recommendation_status(rec_id, 'rejected')
        
        # 키워드 블랙리스트에 추가
        if keyword:
            self.sqlite.add_to_blacklist(user_id, keyword)
            logger.info(f"키워드 '{keyword}'가 사용자 {user_id}의 블랙리스트에 추가되었습니다.")
        
        logger.info(f"❌ 추천 {rec_id} 거절 처리 완료")
        return True, "추천이 거절되었습니다. 해당 키워드는 더 이상 추천되지 않습니다."
    
    async def _generate_report(
        self,
        keyword: str,
        related_keywords: List[str],
        user_id: int
    ) -> str:
        """LLM을 사용하여 요약 리포트를 생성합니다."""
        # 사용자 관심사와 설문 정보 조회
        survey_data = self.sqlite.get_survey_response(user_id)
        
        context = ""
        if survey_data:
            job_field = survey_data.get('job_field_other') or survey_data.get('job_field', '')
            if job_field:
                context = f"사용자 직업/분야: {job_field}"
        
        prompt = f"""당신은 사용자에게 맞춤형 정보를 제공하는 AI 어시스턴트입니다.

## 키워드 정보
- 핵심 키워드: {keyword}
- 관련 키워드: {', '.join(related_keywords) if related_keywords else '없음'}
{f'- {context}' if context else ''}

## 요청
위 키워드에 대해 사용자가 알면 좋을 **핵심 정보를 3~5줄로 요약**해서 Markdown 형식으로 작성해 주세요.

## 작성 가이드라인
1. 친근하고 이해하기 쉬운 한국어로 작성
2. 핵심 개념이나 최신 트렌드 위주로 설명
3. 이모지를 적절히 활용하여 가독성 향상
4. 필요하다면 간단한 팁이나 추천 리소스 포함

## 출력 형식
Markdown 형식의 요약 리포트 (3~5줄)
"""

        try:
            # 리포트 생성용 모델 설정 (일반 텍스트 출력)
            report_model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 512,
                    "response_mime_type": "text/plain",
                },
                safety_settings=self.safety_settings,
            )
            
            response = report_model.generate_content(
                prompt,
                request_options={"timeout": 20}
            )
            
            report_text = self._extract_llm_response_text(response)
            if report_text:
                return report_text
            
        except Exception as e:
            logger.error(f"리포트 생성 중 오류: {e}", exc_info=True)
        
        # Fallback 리포트
        return f"""## {keyword} 📌

**{keyword}**에 대해 관심을 가지고 계시네요!

관련 키워드: {', '.join(related_keywords) if related_keywords else '없음'}

더 자세한 정보가 필요하시면 채팅으로 질문해 주세요! 🔍
"""
    
    # ============================================================
    # UI Support Methods
    # ============================================================
    
    def get_pending_recommendations(self, user_id: int) -> List[Dict[str, Any]]:
        """
        대기 중인 추천 목록을 반환합니다.
        
        Args:
            user_id: 사용자 ID
        
        Returns:
            status='pending'인 추천 목록
        """
        try:
            return self.sqlite.get_pending_recommendations(user_id)
        except Exception as e:
            logger.error(f"대기 중 추천 조회 오류: {e}")
            return []
    
    def get_recommendation(self, recommendation_id: int) -> Optional[Dict[str, Any]]:
        """추천 상세 정보를 조회합니다."""
        try:
            return self.sqlite.get_recommendation(recommendation_id)
        except Exception as e:
            logger.error(f"추천 조회 오류: {e}")
            return None
    
    def get_all_recommendations(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """사용자의 모든 추천 내역을 조회합니다."""
        try:
            return self.sqlite.get_all_recommendations(user_id, limit)
        except Exception as e:
            logger.error(f"모든 추천 조회 오류: {e}")
            return []
    
    # ============================================================
    # Legacy Compatibility (run_periodic_analysis wrapper)
    # ============================================================
    
    async def run_periodic_analysis(self, user_id: int, recommendation_type: str = 'scheduled') -> Tuple[bool, str]:
        """
        기존 run_periodic_analysis 메서드와의 호환성을 위한 래퍼.
        내부적으로 run_active_analysis를 호출합니다.
        """
        return await self.run_active_analysis(user_id)
