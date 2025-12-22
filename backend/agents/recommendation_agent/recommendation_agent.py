"""
RecommendationAgent - 능동형 추천 에이전트 (Active Agent)

LLM(Gemini)이 사용자의 로그와 관심사(Survey)를 분석하여 적절한 타이밍에
"말풍선 메시지"를 제안하는 방식으로 동작합니다.
"""

import json
import logging
import re
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
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "block_none"},
            ]
            self.llm_model = genai.GenerativeModel(
                model_name="gemini-2.5-pro",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_output_tokens": 4096,  # 토큰 제한으로 JSON 잘림 방지
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
    
    # ============================================================
    # Core Active Analysis Methods
    # ============================================================
    
    async def run_active_analysis(self, user_id: int, force_recommend: bool = False) -> Tuple[bool, str]:
        """
        능동형 분석 실행 - 주기적으로 호출되어 추천을 생성합니다.
        
        Args:
            user_id: 사용자 ID
            force_recommend: True면 데이터가 있을 경우 무조건 추천 생성 (초기 분석용)
        
        Returns:
            Tuple[bool, str]: (성공 여부, 메시지)
        """
        
        if not self.llm_available:
            logger.warning(f"[User {user_id}] LLM 서비스 사용 불가")
            return False, "LLM 서비스를 사용할 수 없습니다."
        
        try:
            # Step 1: Data Preparation
            # 최근 브라우저 로그와 콘텐츠 키워드 조회
            browser_logs = self.sqlite.get_browser_logs(user_id, limit=50)
            content_keywords = self.sqlite.get_content_keywords(user_id, limit=100)
            
            logger.debug(f"[User {user_id}] 브라우저 로그: {len(browser_logs)}개, 콘텐츠 키워드: {len(content_keywords)}개")
            
            if not browser_logs and not content_keywords:
                logger.info(f"[User {user_id}] 분석할 활동 데이터 없음")
                return False, "분석할 새로운 활동 데이터가 없습니다."
            
            # 참조 데이터 조회
            blacklist = self.sqlite.get_blacklist(user_id)
            user_interests = self.sqlite.get_user_interests(user_id)
            survey_data = self.sqlite.get_survey_response(user_id)
            
            # 기존 추천 키워드 조회 (중복 추천 방지)
            all_recommendations = self.sqlite.get_all_recommendations(user_id, limit=100)
            past_recommended_keywords = [
                rec.get('keyword', '').lower() 
                for rec in all_recommendations 
                if rec.get('keyword')
            ]
            
            # 기존 추천이 없으면 force_recommend 활성화 (초기 분석)
            existing_recommendations = self.sqlite.get_pending_recommendations(user_id)
            if not existing_recommendations and not user_interests:
                force_recommend = True
            
            # Step 2: LLM Analysis & Decision
            logger.info(f"[User {user_id}] LLM 분석 시작 (force_recommend={force_recommend})")
            analysis_result = await self._analyze_with_llm(
                browser_logs=browser_logs,
                content_keywords=content_keywords,
                blacklist=blacklist,
                user_interests=user_interests,
                survey_data=survey_data,
                force_recommend=force_recommend,
                past_recommended_keywords=past_recommended_keywords
            )
            
            if not analysis_result:
                logger.info(f"[User {user_id}] LLM 분석 결과 없음")
                return False, "LLM 분석 결과가 없습니다."
            
            if not analysis_result.get('should_recommend'):
                logger.info(f"[User {user_id}] LLM이 추천 생성 불필요로 판단: {analysis_result.get('reason', 'unknown')}")
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
            
            # WebSocket으로 실시간 알림 전송
            try:
                from core.websocket_manager import get_websocket_manager
                ws_manager = get_websocket_manager()
                
                # 생성된 추천 정보 조회
                recommendation = self.sqlite.get_recommendation(user_id, rec_id)
                if recommendation and ws_manager.is_user_connected(user_id):
                    # user_id를 추천 객체에 추가 (WebSocket에서 사용)
                    recommendation['user_id'] = user_id
                    await ws_manager.broadcast_recommendation(user_id, recommendation)
            except Exception:
                pass  # WebSocket 알림 전송 실패 무시
            
            return True, f"새로운 추천이 생성되었습니다: {analysis_result.get('keyword')}"
            
        except Exception as e:
            logger.error(f"User {user_id} 능동형 분석 중 오류: {e}", exc_info=True)
            return False, f"분석 중 오류가 발생했습니다: {e}"
    
    async def _analyze_with_llm(
        self,
        browser_logs: List[Dict[str, Any]],
        content_keywords: List[Dict[str, Any]],
        blacklist: List[str],
        user_interests: List[Dict[str, Any]],
        survey_data: Optional[Dict[str, Any]],
        force_recommend: bool = False,
        past_recommended_keywords: List[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        LLM을 사용하여 로그를 분석하고 추천 여부를 결정합니다.
        
        Args:
            force_recommend: True면 데이터가 있을 경우 무조건 추천 생성
            past_recommended_keywords: 이미 추천했던 키워드 목록 (중복 추천 방지)
        
        Returns:
            분석 결과 딕셔너리 또는 None
        """
        if past_recommended_keywords is None:
            past_recommended_keywords = []
        # 로그 요약 생성
        log_summary = self._prepare_log_summary(browser_logs, content_keywords)
        
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
        
        # force_recommend 모드: 데이터가 있으면 무조건 추천 생성
        force_instruction = ""
        if force_recommend:
            force_instruction = """
## 🔴 중요: 강제 추천 모드
이것은 초기 분석입니다. 로그에 어떤 데이터든 있다면 **반드시 should_recommend를 true로 설정**하고 
가장 흥미로운 주제에 대해 추천을 생성하세요. 새로운 관심사인지 기존 관심사인지는 중요하지 않습니다.
사용자에게 유용한 정보를 제공하는 것이 목표입니다.
"""
        
        prompt = f"""당신은 사용자의 활동을 분석하여 맞춤형 추천을 제안하는 AI 어시스턴트입니다.
{force_instruction}
## 사용자 활동 로그
{log_summary}

## 기존 관심사
{', '.join(existing_interests) if existing_interests else '등록된 관심사 없음'}

{survey_info}

## 블랙리스트 (추천 제외 키워드)
{', '.join(blacklist) if blacklist else '없음'}

## 이미 추천한 키워드 (중복 추천 금지)
{', '.join(past_recommended_keywords) if past_recommended_keywords else '없음'}

## 분석 지시사항
1. 로그에서 의미 있는 키워드와 주제를 추출하세요.
2. 블랙리스트에 있는 키워드는 절대 추천하지 마세요.
3. **이미 추천한 키워드와 동일하거나 매우 유사한 키워드는 추천하지 마세요.** 새로운 주제를 찾아주세요.
5. 다음 세 가지 케이스 중 하나를 판단하세요:
   - **Case A (new_interest)**: 기존 관심사에 없던 새로운 주제가 발견된 경우
   - **Case B (periodic_expansion)**: 기존 관심사를 더 깊게 탐구하는 활동이 감지된 경우
   - **Case C (initial_discovery)**: 초기 분석으로, 사용자의 주요 관심사를 파악한 경우
6. 로그에 데이터가 있다면 가능한 한 추천을 생성하세요. should_recommend를 false로 설정하는 것은 정말 추천할 내용이 없을 때만입니다.
7. 추천 시, 사용자에게 건넬 **친근한 한국어 말풍선 메시지**를 작성하세요.
   - 예시: "요즘 Python에 관심이 많으시네요! 관련 자료를 찾아볼까요? 🐍"

## 출력 형식 (JSON) - 반드시 한 줄로 compact하게 출력
{{"should_recommend":true,"trigger_type":"new_interest","keyword":"핵심키워드","related_keywords":["관련1","관련2"],"bubble_message":"친근한 메시지","reasoning":"판단근거"}}

만약 추천할 내용이 없다면:
{{"should_recommend":false,"reasoning":"추천하지 않는 이유"}}

⚠️ 중요: JSON은 반드시 한 줄로, 줄바꿈 없이 compact하게 출력하세요.
"""

        try:
            response = self.llm_model.generate_content(
                prompt,
                request_options={"timeout": 90}
            )
            
            # prompt_feedback 확인 (안전 필터 차단 여부)
            prompt_feedback = getattr(response, 'prompt_feedback', None)
            if prompt_feedback:
                block_reason = getattr(prompt_feedback, 'block_reason', None)
                if block_reason:
                    logger.warning("Gemini 응답이 차단됨 - block_reason: %s", block_reason)
                    return None
            
            # 응답 파싱
            result_text = self._extract_llm_response_text(response)
            if not result_text:
                logger.warning("LLM 응답 텍스트가 비어있습니다.")
                return None
            
            # JSON 파싱 (로버스트 처리)
            result = self._parse_json_safely(result_text)
            if result is None:
                logger.warning("LLM 응답에서 유효한 JSON을 추출하지 못했습니다.")
            return result
            
        except Exception as e:
            logger.error(f"LLM 분석 중 오류: {e}", exc_info=True)
            return None
    
    def _prepare_log_summary(
        self,
        browser_logs: List[Dict[str, Any]],
        content_keywords: List[Dict[str, Any]]
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
        
        if content_keywords:
            lines.append("\n### 추출된 핵심 키워드")
            # 소스별로 그룹화
            keywords_by_source = {}
            for kw in content_keywords[:50]:  # 최대 50개
                source_type = kw.get('source_type', 'unknown')
                keyword = kw.get('keyword', '')
                if source_type not in keywords_by_source:
                    keywords_by_source[source_type] = []
                if keyword not in keywords_by_source[source_type]:
                    keywords_by_source[source_type].append(keyword)
            
            for source_type, keywords in keywords_by_source.items():
                source_label = {'file': '파일', 'web': '웹', 'chat': '채팅'}.get(source_type, source_type)
                lines.append(f"- {source_label}: {', '.join(keywords[:15])}")  # 각 소스당 최대 15개
        
        return '\n'.join(lines) if lines else "활동 로그 없음"
    
    def _extract_llm_response_text(self, response) -> Optional[str]:
        """Gemini 응답에서 텍스트를 안전하게 추출합니다."""
        try:
            if response is None:
                logger.warning("Gemini 응답이 None입니다.")
                return None
            
            # 먼저 response.text를 시도 (가장 간단하고 안정적인 방법)
            try:
                text = getattr(response, "text", None)
                if text and text.strip():
                    return text.strip()
            except Exception as e:
                logger.debug(f"response.text 접근 실패: {e}")
            
            # Fallback: candidates에서 추출
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                # 응답은 있지만 candidates가 없는 경우 - 안전 필터 차단 가능성
                logger.warning("Gemini 응답에 candidates가 없습니다. 응답 객체: %s", type(response))
                return None
            
            candidate = candidates[0]
            
            # finish_reason 확인
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason and str(finish_reason) not in ["STOP", "1", "FinishReason.STOP"]:
                logger.warning(f"Gemini 응답 종료 이유: {finish_reason}")
            
            content_parts = getattr(getattr(candidate, "content", None), "parts", None) or []
            
            extracted_chunks = []
            for part in content_parts:
                text_chunk = getattr(part, "text", None)
                if text_chunk:
                    extracted_chunks.append(text_chunk)
            
            if extracted_chunks:
                return "\n".join(extracted_chunks).strip()
            
            logger.warning("Gemini 응답에서 텍스트를 추출할 수 없습니다.")
            return None
            
        except Exception as e:
            logger.error(f"LLM 응답 추출 오류: {e}")
            return None
    
    def _parse_json_safely(self, text: str) -> Optional[Dict[str, Any]]:
        """
        LLM 응답에서 JSON을 안전하게 추출하고 파싱합니다.
        
        - 마크다운 코드 블록 제거
        - JSON 객체만 추출
        - 불완전한 JSON 복구 시도 (토큰 제한으로 잘린 경우)
        """
        if not text or not text.strip():
            logger.warning("LLM 응답이 비어있습니다.")
            return None
        
        text = text.strip()
        
        try:
            # 1단계: 마크다운 코드 블록 제거 (```json ... ``` 또는 ``` ... ```)
            code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
            code_match = re.search(code_block_pattern, text)
            if code_match:
                text = code_match.group(1).strip()
            
            # 2단계: JSON 객체 추출 시도 (가장 바깥쪽 중괄호)
            json_match = re.search(r'\{[\s\S]*\}', text)
            
            if json_match:
                json_str = json_match.group()
            elif text.startswith('{'):
                # 닫는 중괄호가 없는 잘린 JSON일 수 있음 (토큰 제한으로 잘림)
                logger.info("닫는 중괄호가 없는 잘린 JSON 감지, 복구 시도...")
                json_str = text
            else:
                logger.warning(f"LLM 응답에서 JSON 객체를 찾을 수 없습니다. 응답 시작부분: {text[:200]}...")
                return None
            
            # 3단계: 직접 파싱 시도
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
            
            # 4단계: 불완전한 JSON 복구 시도
            fixed_json = self._fix_truncated_json(json_str)
            if fixed_json:
                try:
                    result = json.loads(fixed_json)
                    logger.info("잘린 JSON 복구 성공")
                    return result
                except json.JSONDecodeError as e:
                    logger.debug(f"복구된 JSON 파싱 실패: {e}")
            
            # 5단계: 최소한의 필수 필드만 추출 시도
            minimal_result = self._extract_minimal_json(json_str)
            if minimal_result:
                logger.info("최소 필드 추출로 JSON 복구 성공")
                return minimal_result
            
            logger.warning(f"JSON 파싱 최종 실패. 원본 시작부분: {json_str[:300]}...")
            return None
            
        except json.JSONDecodeError as e:
            logger.error(f"LLM 응답 JSON 파싱 오류: {e}")
            logger.debug(f"원본 텍스트: {text[:500]}...")
            return None
        except Exception as e:
            logger.error(f"JSON 파싱 중 예상치 못한 오류: {e}")
            return None
    
    def _extract_minimal_json(self, text: str) -> Optional[Dict[str, Any]]:
        """
        잘린 JSON에서 최소한의 필수 필드만 추출합니다.
        
        추천 분석에 필요한 필드: should_recommend, trigger_type, keyword, bubble_message
        """
        try:
            result = {}
            
            # should_recommend 추출
            should_match = re.search(r'"should_recommend"\s*:\s*(true|false)', text, re.IGNORECASE)
            if should_match:
                result['should_recommend'] = should_match.group(1).lower() == 'true'
            else:
                return None  # 필수 필드 없음
            
            # trigger_type 추출
            trigger_match = re.search(r'"trigger_type"\s*:\s*"([^"]+)"', text)
            if trigger_match:
                result['trigger_type'] = trigger_match.group(1)
            
            # keyword 추출
            keyword_match = re.search(r'"keyword"\s*:\s*"([^"]+)"', text)
            if keyword_match:
                result['keyword'] = keyword_match.group(1)
            
            # bubble_message 추출
            bubble_match = re.search(r'"bubble_message"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', text)
            if bubble_match:
                result['bubble_message'] = bubble_match.group(1).replace('\\"', '"')
            
            # related_keywords 추출 시도
            related_match = re.search(r'"related_keywords"\s*:\s*\[(.*?)\]', text, re.DOTALL)
            if related_match:
                keywords_str = related_match.group(1)
                keywords = re.findall(r'"([^"]+)"', keywords_str)
                result['related_keywords'] = keywords[:5]  # 최대 5개
            else:
                result['related_keywords'] = []
            
            # reasoning 추출 (선택)
            reasoning_match = re.search(r'"reasoning"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', text)
            if reasoning_match:
                result['reasoning'] = reasoning_match.group(1).replace('\\"', '"')
            
            # 필수 필드 검증
            if result.get('should_recommend') and result.get('keyword'):
                return result
            elif not result.get('should_recommend'):
                # should_recommend가 false면 keyword 없어도 됨
                return result
            
            return None
            
        except Exception as e:
            logger.debug(f"최소 JSON 추출 실패: {e}")
            return None
    
    def _fix_truncated_json(self, json_str: str) -> Optional[str]:
        """
        잘린 JSON 문자열을 복구 시도합니다.
        
        - 열린 문자열 닫기
        - 누락된 괄호 추가
        """
        try:
            # 열린 따옴표가 닫히지 않은 경우 처리
            # 마지막 열린 따옴표 이후의 내용을 찾아서 닫기
            
            # 따옴표 개수 세기 (이스케이프된 따옴표 제외)
            in_string = False
            last_quote_pos = -1
            i = 0
            while i < len(json_str):
                char = json_str[i]
                if char == '\\' and i + 1 < len(json_str):
                    i += 2  # 이스케이프 문자 건너뛰기
                    continue
                if char == '"':
                    in_string = not in_string
                    if in_string:
                        last_quote_pos = i
                i += 1
            
            # 문자열이 열린 상태로 끝난 경우
            if in_string and last_quote_pos >= 0:
                # 마지막 열린 따옴표 이후 줄바꿈 또는 끝까지의 내용에 따옴표 추가
                newline_pos = json_str.find('\n', last_quote_pos)
                if newline_pos > 0:
                    # 줄바꿈 전에 따옴표 닫기
                    json_str = json_str[:newline_pos] + '"' + json_str[newline_pos:]
                else:
                    # 끝에 따옴표 추가
                    json_str = json_str.rstrip() + '"'
            
            # 괄호 균형 맞추기
            open_braces = json_str.count('{') - json_str.count('}')
            open_brackets = json_str.count('[') - json_str.count(']')
            
            # 누락된 닫는 괄호 추가
            if open_braces > 0 or open_brackets > 0:
                # 마지막 유효 위치 찾기 (쉼표나 값 이후)
                json_str = json_str.rstrip()
                if json_str.endswith(','):
                    json_str = json_str[:-1]
                
                json_str += ']' * open_brackets
                json_str += '}' * open_braces
            
            return json_str
            
        except Exception as e:
            logger.debug(f"JSON 복구 실패: {e}")
            return None
    
    # ============================================================
    # Interaction Handling
    # ============================================================
    
    async def handle_response(self, user_id: int, recommendation_id: int, action: str) -> Tuple[bool, str]:
        """
        UI에서 사용자가 추천에 응답했을 때 처리합니다.
        
        Args:
            user_id: 사용자 ID
            recommendation_id: 추천 ID
            action: 'accept' 또는 'reject'
        
        Returns:
            Tuple[bool, str]: (성공 여부, 결과 메시지 또는 리포트)
        """
        
        # 추천 정보 조회
        recommendation = self.sqlite.get_recommendation(user_id, recommendation_id)
        if not recommendation:
            return False, "추천을 찾을 수 없습니다."
        
        # user_id를 추천 객체에 추가
        recommendation['user_id'] = user_id
        
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
        self.sqlite.update_recommendation_status(user_id, rec_id, 'accepted')
        
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
        self.sqlite.update_recommendation_report(user_id, rec_id, report_content)
        
        # 관심사 점수 상향 조정
        if keyword:
            self.sqlite.upsert_interest(
                user_id=user_id,
                keyword=keyword,
                score=0.8,
                source='user_accepted'
            )
        
        return True, report_content
    
    async def _handle_reject(self, recommendation: Dict[str, Any]) -> Tuple[bool, str]:
        """추천 거절 처리 - 블랙리스트 추가"""
        rec_id = recommendation['id']
        user_id = recommendation['user_id']
        keyword = recommendation.get('keyword', '')
        
        # 상태 업데이트
        self.sqlite.update_recommendation_status(user_id, rec_id, 'rejected')
        
        # 키워드 블랙리스트에 추가
        if keyword:
            self.sqlite.add_to_blacklist(user_id, keyword)
        
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
        
        prompt = f"""당신은 특정 주제에 대해 간결하고 핵심적인 요약 정보를 제공하는 AI 어시스턴트입니다.

## 조사 주제
- 핵심 키워드: {keyword}
- 관련 키워드: {', '.join(related_keywords) if related_keywords else '없음'}
{f'- {context}' if context else ''}

## 요청
위 키워드에 대해 당신의 지식을 바탕으로 전문적이고 유용한 정보를 **간결하고 요약된 형태**로 핵심 정보만 제공해 주세요.
자세한 내용은 별도의 보고서에서 다룰 예정이므로, 여기서는 개요와 핵심만 설명해 주세요.

## 작성 가이드라인
1. **간결성**: 각 섹션은 2-3문장으로 간단히 요약
2. **핵심만**: 가장 중요한 정의, 특징, 활용 분야만 포함
3. **읽기 쉬운 형식**: 불릿 포인트나 짧은 문장 사용
4. **한국어**로 작성하되, 전문 용어는 영어 원어를 병기
5. 이모지를 적절히 활용

## 출력 형식
반드시 다음 구조와 형식을 정확히 따라 작성해 주세요:

## {keyword} 📌

### 개요
키워드의 간단한 정의와 기본 소개 (1-2문장)

### 핵심 내용
- 주요 특징이나 개념을 불릿 포인트로 2-3개 나열

### 활용 분야
주요 활용 분야나 관련 분야를 1-2문장으로 설명

---

중요: 위 형식을 정확히 따라 작성해 주세요. 추가적인 안내 문구나 설명은 포함하지 마세요.
"""

        try:
            # 리포트 생성용 모델 설정 (일반 텍스트 출력)
            report_safety_settings = [
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "block_none"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "block_none"},
            ]
            report_model = genai.GenerativeModel(
                model_name="gemini-2.5-pro",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 4096,
                    "response_mime_type": "text/plain",
                },
                safety_settings=report_safety_settings,
            )
            
            response = report_model.generate_content(
                prompt,
                request_options={"timeout": 90}
            )
            
            report_text = self._extract_llm_response_text(response)
            if report_text and report_text.strip():
                # 보고서 안내 문구 추가
                report_with_footer = f"""{report_text}

---
💡 **더 자세한 내용이 필요하신가요?**
이 주제에 대한 심층 보고서를 작성해서 파일로 저장해 드릴 수 있습니다. {keyword}에 대한 보고서를 작성해드릴까요?
"""
                return report_with_footer
            
        except Exception as e:
            logger.error(f"리포트 생성 중 오류: {e}")
        
        # Fallback 리포트
        return f"""## {keyword} 📌

### 개요
**{keyword}**에 대해 관심을 가지고 계시네요! 현재 정보를 불러오는 데 문제가 발생했습니다.

### 관련 키워드
{', '.join(related_keywords) if related_keywords else '관련 키워드 없음'}

---
💡 **더 자세한 내용이 필요하신가요?**
이 주제에 대한 심층 보고서를 작성해서 파일로 저장해 드릴 수 있습니다. {keyword}에 대한 보고서를 작성해드릴까요?
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
    
    def get_recommendation(self, user_id: int, recommendation_id: int) -> Optional[Dict[str, Any]]:
        """추천 상세 정보를 조회합니다."""
        try:
            return self.sqlite.get_recommendation(user_id, recommendation_id)
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
