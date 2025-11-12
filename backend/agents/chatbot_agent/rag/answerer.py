import os
import base64
import logging
from typing import List, Dict, Any, Optional
from PIL import Image
import io
from datetime import datetime, timedelta
import threading

logger = logging.getLogger(__name__)

# 사용자 프로필 캐시 (메모리 기반)
_user_profile_cache = {}
_cache_lock = threading.Lock()
CACHE_EXPIRY_HOURS = 1  # 1시간 캐시 유지

def _get_cached_user_profile(user_id: int) -> Optional[str]:
    """캐시에서 사용자 프로필 조회"""
    with _cache_lock:
        if user_id in _user_profile_cache:
            cache_entry = _user_profile_cache[user_id]
            # 캐시 만료 확인
            if datetime.now() < cache_entry['expires_at']:
                logger.debug(f"사용자 {user_id} 프로필 캐시 히트")
                return cache_entry['profile']
            else:
                # 만료된 캐시 제거
                del _user_profile_cache[user_id]
                logger.debug(f"사용자 {user_id} 프로필 캐시 만료로 제거")
    return None

def _cache_user_profile(user_id: int, profile: str):
    """사용자 프로필을 캐시에 저장"""
    with _cache_lock:
        _user_profile_cache[user_id] = {
            'profile': profile,
            'expires_at': datetime.now() + timedelta(hours=CACHE_EXPIRY_HOURS)
        }
        logger.debug(f"사용자 {user_id} 프로필 캐시 저장")

def _clear_expired_cache():
    """만료된 캐시 항목들 정리"""
    with _cache_lock:
        current_time = datetime.now()
        expired_keys = [
            user_id for user_id, cache_entry in _user_profile_cache.items()
            if current_time >= cache_entry['expires_at']
        ]
        for key in expired_keys:
            del _user_profile_cache[key]
        if expired_keys:
            logger.debug(f"만료된 캐시 {len(expired_keys)}개 항목 정리")

def _clean_search_results(evidences: List[Dict[str, Any]]) -> str:
    """검색 결과를 깔끔하게 정리하여 컨텍스트 생성"""
    if not evidences:
        return ""
    
    context_parts = []
    seen_content = set()  # 중복 내용 방지
    
    for i, evidence in enumerate(evidences[:3], 1):  # 상위 3개만 사용
        snippet = evidence.get('snippet', '')
        if not snippet or snippet in seen_content:
            continue
            
        seen_content.add(snippet)
        
        # 불필요한 태그나 특수문자 제거
        snippet = snippet.replace('<!-- image -->', '').replace('#', '').strip()
        if len(snippet) > 200:  # 너무 긴 내용은 잘라내기
            snippet = snippet[:200] + "..."
        
        context_parts.append(f"{i}. {snippet}")
    
    return "\n\n".join(context_parts)

# ==============================================================================
# 보안 관련 함수들
# ==============================================================================

def _get_security_patterns() -> List[str]:
    """민감한 정보 패턴 목록을 반환합니다."""
    return [
        r'\b\d{3}-\d{4}-\d{4}\b',  # 전화번호
        r'\b\d{6}-\d{7}\b',        # 주민등록번호
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # 이메일
        r'\b\d{4}-\d{2}-\d{2}\b',  # 날짜 (YYYY-MM-DD)
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',  # IP 주소
    ]

def _redact_sensitive_info(text: str, patterns: List[str]) -> str:
    """민감한 정보를 마스킹합니다."""
    import re
    redacted_text = text
    for pattern in patterns:
        redacted_text = re.sub(pattern, '[REDACTED]', redacted_text)
    return redacted_text

# ==============================================================================
# 이미지 처리 함수들
# ==============================================================================

def images_to_base64(images: List[Image.Image]) -> List[str]:
    """PIL Image 객체들을 base64 문자열로 변환합니다."""
    try:
        base64_images = []
        for img in images:
            # 이미지를 RGB로 변환 (RGBA인 경우)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            
            # JPEG로 인코딩
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            img_bytes = buffer.getvalue()
            
            # base64 인코딩
            img_b64 = base64.b64encode(img_bytes).decode('utf-8')
            base64_images.append(f"data:image/jpeg;base64,{img_b64}")
        
        return base64_images
    except Exception as e:
        logger.error(f"이미지 base64 변환 오류: {e}")
        return []

# ==============================================================================
# Gemini LLM 호출 함수
# ==============================================================================

def call_llm_for_answer(question: str, context: Optional[str], user_profile: Optional[Any] = None) -> Optional[str]:
    """Gemini 모델을 사용하여 답변을 생성합니다."""
    try:
        # Gemini API 호출
        try:
            import google.generativeai as genai
            from config.settings import settings
            
            # API 키 확인
            if not settings.GEMINI_API_KEY:
                logger.warning("GEMINI_API_KEY가 설정되지 않았습니다.")
                return None
            
            # Gemini 모델 초기화 (최적화된 설정 + 안전성 설정)
            genai.configure(api_key=settings.GEMINI_API_KEY)

            # 안전성 설정을 완화하여 스트림 차단 여부를 진단 (문제 해결 후 재조정 필요)
            temp_safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 40,
                    "max_output_tokens": 200,  # 응답 길이 제한
                    "response_mime_type": "text/plain"
                },
                safety_settings=temp_safety_settings
            )
            
            # 사용자 프로필 컨텍스트 생성 (dict 또는 문자열 형태 모두 지원)
            user_context_prompt = ""
            if user_profile:
                if isinstance(user_profile, dict):
                    # dict 형태의 프로필을 구조화된 형태로 변환
                    profile_parts = []
                    
                    # 관심 주제
                    interests = user_profile.get('interests', [])
                    if interests:
                        interest_mapping = {
                            'tech': 'IT/최신 기술',
                            'finance': '경제/금융/투자',
                            'ai': '인공지능/데이터 과학',
                            'design': '디자인/예술',
                            'marketing': '마케팅/비즈니스',
                            'productivity': '생산성/자기계발',
                            'health': '건강/운동',
                            'travel': '여행/문화'
                        }
                        interest_texts = [interest_mapping.get(interest, interest) for interest in interests]
                        profile_parts.append(f"관심 주제: {', '.join(interest_texts)}")
                    
                    # 직업 분야
                    job_field = user_profile.get('job_field', '')
                    job_field_other = user_profile.get('job_field_other', '')
                    if job_field == 'other' and job_field_other:
                        profile_parts.append(f"직업: {job_field_other}")
                    elif job_field:
                        job_mapping = {
                            'student': '학생',
                            'developer': '개발자/엔지니어',
                            'designer': '디자이너',
                            'planner': '기획자/마케터',
                            'researcher': '연구원/교육자'
                        }
                        profile_parts.append(f"직업: {job_mapping.get(job_field, job_field)}")
                    
                    # 도움 받고 싶은 영역
                    help_preferences = user_profile.get('help_preferences', [])
                    if help_preferences:
                        help_mapping = {
                            'work_search': '업무 관련 정보 검색 및 요약',
                            'inspiration': '새로운 아이디어나 영감 얻기',
                            'writing': '글쓰기 보조',
                            'learning': '개인적인 학습 및 지식 확장'
                        }
                        help_texts = [help_mapping.get(help, help) for help in help_preferences]
                        profile_parts.append(f"도움 받고 싶은 영역: {', '.join(help_texts)}")
                    
                    # 사용자 정의 키워드
                    custom_keywords = user_profile.get('custom_keywords', '')
                    if custom_keywords:
                        profile_parts.append(f"특별 관심 키워드: {custom_keywords}")
                    
                    if profile_parts:
                        profile_summary = " | ".join(profile_parts)
                        user_context_prompt = f"""
[User Context]
You are responding to a user you know. Use the following context to personalize your answer:

{profile_summary}

---
"""
                elif isinstance(user_profile, str):
                    # 문자열 형태의 프로필 (하위 호환성)
                    user_context_prompt = f"""
[User Context]
You are responding to a user you know. Use the following context to personalize your answer:

{user_profile[:300]}

---
"""
            
            # 프롬프트 구성 (간소화 + 품질 개선)
            if context:
                # STANDARD_RAG 또는 SUMMARY_QUERY 경로
                prompt = f"""{user_context_prompt}검색된 정보를 바탕으로 질문에 간결하게 답변하세요.

정보:
{context[:600]}  # 컨텍스트 길이 제한

규칙:
- 검색된 정보만 사용하여 답변
- 2-3문장으로 간결하게
- 중복 내용 제거
- 핵심만 전달

질문: {question}

답변:"""
            else:
                # GENERAL_CHAT 경로
                prompt = f"""{user_context_prompt}일반적인 질문에 간결하고 친근하게 답변하세요.

질문: {question}

답변:"""

            # Gemini API 호출 (타임아웃 설정 + 오류 처리 개선)
            try:
                response = model.generate_content(
                    prompt,
                    request_options={"timeout": 5}  # 5초 타임아웃
                )
                
                if response and response.text:
                    logger.info("Gemini 답변 생성 성공")
                    return response.text.strip()
                else:
                    # finish_reason 확인
                    if hasattr(response, 'candidates') and response.candidates:
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'finish_reason'):
                            if candidate.finish_reason == 2:  # SAFETY
                                logger.warning("Gemini API가 안전성 정책으로 인해 응답을 차단했습니다.")
                                return "죄송합니다. 해당 질문에 대해 안전한 답변을 제공할 수 없습니다."
                            elif candidate.finish_reason == 3:  # RECITATION
                                logger.warning("Gemini API가 저작권 정책으로 인해 응답을 차단했습니다.")
                                return "죄송합니다. 저작권 정책으로 인해 해당 내용을 제공할 수 없습니다."
                    
                    logger.warning("Gemini 응답이 비어있습니다.")
                    return None
                    
            except Exception as api_error:
                logger.error(f"Gemini API 호출 중 오류: {api_error}")
                return None
                
        except ImportError:
            logger.warning("google-generativeai 라이브러리가 설치되지 않았습니다.")
            return None
        except Exception as e:
            logger.error(f"Gemini API 호출 오류: {e}")
            return None
            
    except Exception as e:
        logger.error(f"LLM 호출 오류: {e}")
        return None

def call_llm_for_answer_stream(question: str, context: Optional[str], user_profile: Optional[Any] = None):
    """Gemini 모델을 사용하여 스트리밍 방식으로 답변을 생성합니다 (제너레이터)."""
    try:
        # Gemini API 호출
        try:
            import google.generativeai as genai
            from config.settings import settings
            
            # API 키 확인
            if not settings.GEMINI_API_KEY:
                logger.warning("GEMINI_API_KEY가 설정되지 않았습니다.")
                yield "죄송합니다. API 키가 설정되지 않았습니다."
                return
            
            # Gemini 모델 초기화 (최적화된 설정 + 안전성 설정)
            genai.configure(api_key=settings.GEMINI_API_KEY)

            # 안전성 설정을 완화하여 스트림 차단 여부를 진단 (문제 해결 후 재조정 필요)
            temp_safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 40,
                    "max_output_tokens": 200,  # 응답 길이 제한
                    "response_mime_type": "text/plain"
                },
                safety_settings=temp_safety_settings
            )
            
            # 사용자 프로필 컨텍스트 생성 (dict 또는 문자열 형태 모두 지원)
            user_context_prompt = ""
            if user_profile:
                if isinstance(user_profile, dict):
                    # dict 형태의 프로필을 구조화된 형태로 변환
                    profile_parts = []
                    
                    # 관심 주제
                    interests = user_profile.get('interests', [])
                    if interests:
                        interest_mapping = {
                            'tech': 'IT/최신 기술',
                            'finance': '경제/금융/투자',
                            'ai': '인공지능/데이터 과학',
                            'design': '디자인/예술',
                            'marketing': '마케팅/비즈니스',
                            'productivity': '생산성/자기계발',
                            'health': '건강/운동',
                            'travel': '여행/문화'
                        }
                        interest_texts = [interest_mapping.get(interest, interest) for interest in interests]
                        profile_parts.append(f"관심 주제: {', '.join(interest_texts)}")
                    
                    # 직업 분야
                    job_field = user_profile.get('job_field', '')
                    job_field_other = user_profile.get('job_field_other', '')
                    if job_field == 'other' and job_field_other:
                        profile_parts.append(f"직업: {job_field_other}")
                    elif job_field:
                        job_mapping = {
                            'student': '학생',
                            'developer': '개발자/엔지니어',
                            'designer': '디자이너',
                            'planner': '기획자/마케터',
                            'researcher': '연구원/교육자'
                        }
                        profile_parts.append(f"직업: {job_mapping.get(job_field, job_field)}")
                    
                    # 도움 받고 싶은 영역
                    help_preferences = user_profile.get('help_preferences', [])
                    if help_preferences:
                        help_mapping = {
                            'work_search': '업무 관련 정보 검색 및 요약',
                            'inspiration': '새로운 아이디어나 영감 얻기',
                            'writing': '글쓰기 보조',
                            'learning': '개인적인 학습 및 지식 확장'
                        }
                        help_texts = [help_mapping.get(help, help) for help in help_preferences]
                        profile_parts.append(f"도움 받고 싶은 영역: {', '.join(help_texts)}")
                    
                    # 사용자 정의 키워드
                    custom_keywords = user_profile.get('custom_keywords', '')
                    if custom_keywords:
                        profile_parts.append(f"특별 관심 키워드: {custom_keywords}")
                    
                    if profile_parts:
                        profile_summary = " | ".join(profile_parts)
                        user_context_prompt = f"""
[User Context]
You are responding to a user you know. Use the following context to personalize your answer:

{profile_summary}

---
"""
                elif isinstance(user_profile, str):
                    # 문자열 형태의 프로필 (하위 호환성)
                    user_context_prompt = f"""
[User Context]
You are responding to a user you know. Use the following context to personalize your answer:

{user_profile[:300]}

---
"""
            
            # 프롬프트 구성 (간소화 + 품질 개선)
            if context:
                # STANDARD_RAG 또는 SUMMARY_QUERY 경로
                prompt = f"""{user_context_prompt}검색된 정보를 바탕으로 질문에 간결하게 답변하세요.

정보:
{context[:600]}  # 컨텍스트 길이 제한

규칙:
- 검색된 정보만 사용하여 답변
- 2-3문장으로 간결하게
- 중복 내용 제거
- 핵심만 전달

질문: {question}

답변:"""
            else:
                # GENERAL_CHAT 경로
                prompt = f"""{user_context_prompt}일반적인 질문에 간결하고 친근하게 답변하세요.

질문: {question}

답변:"""

            # Gemini API 스트리밍 호출
            unified_chunks = []

            def _extract_text_from_chunk(chunk) -> str:
                text_parts = []
                try:
                    if hasattr(chunk, "to_dict"):
                        logger.info("Gemini chunk raw (dict): %s", chunk.to_dict())
                    else:
                        logger.info("Gemini chunk raw (repr): %s", repr(chunk))
                except Exception:
                    logger.info("Gemini chunk raw: <unserializable>")

                if getattr(chunk, "text", None):
                    text_parts.append(chunk.text)

                candidates = getattr(chunk, "candidates", [])
                if candidates:
                    for candidate in candidates:
                        parts = getattr(candidate, "content", None)
                        if parts and getattr(parts, "parts", None):
                            for part in parts.parts:
                                part_text = getattr(part, "text", None)
                                if part_text:
                                    text_parts.append(part_text)
                        if getattr(candidate, "finish_reason", None) == 2:
                            logger.warning("Gemini API가 안전성 정책으로 인해 응답을 차단했습니다.")
                            text_parts.append("죄송합니다. 해당 질문에 대해 안전한 답변을 제공할 수 없습니다.")
                        elif getattr(candidate, "finish_reason", None) == 3:
                            logger.warning("Gemini API가 저작권 정책으로 인해 응답을 차단했습니다.")
                            text_parts.append("죄송합니다. 저작권 정책으로 인해 해당 내용을 제공할 수 없습니다.")

                extracted = "".join(text_parts)
                if extracted:
                    logger.debug("Gemini chunk extracted text: %s", extracted[:200])
                else:
                    logger.debug("Gemini chunk without text: %s", chunk)
                return extracted

            try:
                response = model.generate_content(
                    prompt,
                    stream=True,
                    request_options={"timeout": 5}
                )
                logger.info("Gemini streaming response object type: %s", type(response))

                for idx, chunk in enumerate(response):
                    logger.info("Gemini chunk #%d raw object: %s", idx, repr(chunk))
                    chunk_text = _extract_text_from_chunk(chunk)
                    if chunk_text:
                        unified_chunks.append(chunk_text)
                        yield chunk_text
                    else:
                        logger.info("Gemini chunk #%d produced no text.", idx)

                logger.info("Gemini 스트리밍이 정상적으로 종료되었습니다.")
                if not unified_chunks:
                    logger.warning("Gemini 스트리밍 응답이 비어 있습니다. (unified_chunks=0)")
                    yield "죄송합니다. API가 빈 응답을 반환했습니다. (No chunks)"
                else:
                    logger.info("Gemini 스트리밍 답변 생성 성공 (총 %d개 청크)", len(unified_chunks))

            except StopIteration as stop_err:
                logger.error("Gemini 스트림이 즉시 StopIteration을 반환했습니다. API 키, 결제 상태 또는 안전 설정을 확인하세요.", exc_info=True)
                yield "죄송합니다. API가 빈 스트림을 반환했습니다. (안전 설정 또는 API 키 문제일 수 있습니다)"
                return
            except Exception as api_error:
                logger.error("Gemini API 호출 중 오류 발생", exc_info=True)
                yield f"죄송합니다. 답변 생성 중 오류가 발생했습니다: {api_error}"
                return
                
        except ImportError:
            logger.warning("google-generativeai 라이브러리가 설치되지 않았습니다.")
            yield "죄송합니다. 필요한 라이브러리가 설치되지 않았습니다."
        except Exception as e:
            logger.error(f"Gemini API 호출 오류: {e}")
            yield "죄송합니다. 답변 생성 중 오류가 발생했습니다."
            
    except Exception as e:
        logger.error(f"LLM 호출 오류: {e}")
        yield "응답 생성 중 오류가 발생했습니다."

# ==============================================================================
# 핵심 답변 생성 함수
# ==============================================================================

def compose_answer_sync(question: str, evidences: Optional[List[Dict[str, Any]]], user_id: Optional[int] = None, user_profile: Optional[Dict[str, Any]] = None) -> str:
    """검색된 근거(evidences)를 바탕으로 최종 텍스트 답변을 구성합니다 (동기 버전 - 제너레이터를 문자열로 변환)."""
    chunks = []
    for chunk in compose_answer(question, evidences, user_id, user_profile):
        chunks.append(chunk)
    return "".join(chunks)

def compose_answer(question: str, evidences: Optional[List[Dict[str, Any]]], user_id: Optional[int] = None, user_profile: Optional[Dict[str, Any]] = None):
    """검색된 근거(evidences)를 바탕으로 최종 텍스트 답변을 구성합니다 (스트리밍 제너레이터)."""
    try:
        answer_prefix = "" # 답변 접두사 초기화

        # 사용자 프로필 가져오기 (캐싱 적용 + 최적화)
        # user_profile이 dict로 전달되지 않은 경우에만 user_id로부터 가져옴
        if user_profile is None and user_id:
            # 1. 캐시에서 먼저 확인 (문자열 형태)
            cached_profile_str = _get_cached_user_profile(user_id)
            
            # 2. 캐시에 없으면 DB에서 조회하고 캐시에 저장
            if cached_profile_str is None:
                try:
                    from database.user_profile_indexer import get_global_profile_indexer
                    from database.sqlite_meta import SQLiteMeta
                    indexer = get_global_profile_indexer()
                    sqlite_meta = SQLiteMeta()
                    # dict 형태로 설문 데이터 가져오기
                    user_profile = sqlite_meta.get_user_survey_response(user_id)
                    if user_profile:
                        # 문자열 형태로도 캐시에 저장 (하위 호환성)
                        profile_str = indexer.get_profile_as_context(user_id)
                        if profile_str:
                            if len(profile_str) > 500:
                                profile_str = profile_str[:500] + "..."
                            _cache_user_profile(user_id, profile_str)
                        logger.debug(f"사용자 {user_id} 프로필을 DB에서 조회하여 캐시에 저장")
                    else:
                        logger.debug(f"사용자 {user_id} 프로필이 DB에 없음")
                except Exception as e:
                    logger.warning(f"프로필 로드 실패: {e}")
            else:
                # 캐시에서 문자열을 가져왔지만, dict 형태가 필요하므로 다시 조회
                try:
                    from database.sqlite_meta import SQLiteMeta
                    sqlite_meta = SQLiteMeta()
                    user_profile = sqlite_meta.get_user_survey_response(user_id)
                    logger.debug(f"사용자 {user_id} 프로필을 캐시에서 조회")
                except Exception as e:
                    logger.warning(f"프로필 dict 조회 실패: {e}")
        
        # evidences가 None인 경우 (GENERAL_CHAT 경로)
        if evidences is None:
            try:
                # 스트리밍 방식으로 답변 생성
                for chunk in call_llm_for_answer_stream(question, None, user_profile):
                    yield chunk
                logger.info("Gemini 일반 대화 답변 생성 성공")
                return
            except Exception as e:
                logger.warning(f"Gemini 일반 대화 답변 생성 실패: {e}")
            
            # Gemini가 실패한 경우 기본 답변
            yield f"안녕하세요! '{question}'에 대해 답변드리겠습니다. 더 구체적인 질문이 있으시면 언제든 말씀해주세요."
            return
        
        # evidences가 빈 리스트인 경우, 사용자 프로필을 근거로 사용 시도
        if not evidences:
            logger.warning("검색된 근거가 없어 사용자 프로필로 대체합니다.")
            if user_profile:
                # 사용자 프로필을 evidence 형식으로 변환
                # dict 형태인 경우 문자열로 변환
                if isinstance(user_profile, dict):
                    from database.user_profile_indexer import get_global_profile_indexer
                    indexer = get_global_profile_indexer()
                    profile_snippet = indexer.convert_survey_to_text(user_profile)
                else:
                    profile_snippet = str(user_profile)
                
                evidences = [{
                    'source': 'user_profile',
                    'doc_id': 'profile',
                    'snippet': profile_snippet
                }]
                # 프로필 사용에 대한 안내 문구 추가
                answer_prefix = "요청하신 내용에 대한 구체적인 정보는 찾지 못했습니다. 대신, 저장된 사용자 프로필을 바탕으로 답변해 드릴게요.\n\n"
            else:
                yield "죄송합니다. 관련 정보를 찾을 수 없습니다."
                return

        # 검색된 정보를 깔끔하게 정리
        context = _clean_search_results(evidences)
        
        if not context:
            yield "관련 정보를 찾을 수 없습니다."
            return
        
        # 접두사가 있으면 먼저 yield
        if answer_prefix:
            yield answer_prefix
        
        # Gemini를 사용한 답변 생성 시도 (프로필 포함) - 스트리밍 방식
        try:
            for chunk in call_llm_for_answer_stream(question, context, user_profile):
                yield chunk
            logger.info("Gemini 답변 생성 성공")
        except Exception as e:
            logger.warning(f"Gemini 답변 생성 실패: {e}")
            # Gemini가 실패한 경우, 사용자 친화적인 오류 메시지 반환
            logger.warning("Gemini 답변 생성에 실패하여 사용자에게 오류 메시지를 반환합니다.")
            yield "죄송합니다, 답변을 생성하는 데 문제가 발생했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        logger.error(f"응답 생성 오류: {e}")
        yield "응답 생성 중 오류가 발생했습니다."

