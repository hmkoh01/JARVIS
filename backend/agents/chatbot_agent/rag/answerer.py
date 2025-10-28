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

def call_llm_for_answer(question: str, context: Optional[str], user_profile: Optional[str] = None) -> Optional[str]:
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
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 40,
                    "max_output_tokens": 200,  # 응답 길이 제한
                    "response_mime_type": "text/plain"
                },
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH", 
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    }
                ]
            )
            
            # 시스템 컨텍스트 (사용자 프로필 포함) - 간소화
            system_context = ""
            if user_profile:
                system_context = f"""
사용자 프로필: {user_profile[:300]}  # 프로필 길이 제한
위 프로필을 참고하여 맞춤형 답변을 제공하세요.
"""
            
            # 프롬프트 구성 (간소화 + 품질 개선)
            if context:
                # STANDARD_RAG 또는 SUMMARY_QUERY 경로
                prompt = f"""{system_context}
검색된 정보를 바탕으로 질문에 간결하게 답변하세요.

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
                prompt = f"""{system_context}
일반적인 질문에 간결하고 친근하게 답변하세요.

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

# ==============================================================================
# 핵심 답변 생성 함수
# ==============================================================================

def compose_answer(question: str, evidences: Optional[List[Dict[str, Any]]], user_id: Optional[int] = None) -> str:
    """검색된 근거(evidences)를 바탕으로 최종 텍스트 답변을 구성합니다."""
    try:
        answer_prefix = "" # 답변 접두사 초기화

        # 사용자 프로필 가져오기 (캐싱 적용 + 최적화)
        user_profile = None
        if user_id:
            # 1. 캐시에서 먼저 확인
            user_profile = _get_cached_user_profile(user_id)
            
            # 2. 캐시에 없으면 DB에서 조회하고 캐시에 저장 (간소화된 조회)
            if user_profile is None:
                try:
                    from database.user_profile_indexer import UserProfileIndexer
                    indexer = UserProfileIndexer()
                    user_profile = indexer.get_profile_as_context(user_id)
                    if user_profile:
                        # 프로필이 너무 길면 잘라서 캐시
                        if len(user_profile) > 500:
                            user_profile = user_profile[:500] + "..."
                        _cache_user_profile(user_id, user_profile)
                        logger.debug(f"사용자 {user_id} 프로필을 DB에서 조회하여 캐시에 저장")
                    else:
                        logger.debug(f"사용자 {user_id} 프로필이 DB에 없음")
                except Exception as e:
                    logger.warning(f"프로필 로드 실패: {e}")
            else:
                logger.debug(f"사용자 {user_id} 프로필을 캐시에서 조회")
        
        # evidences가 None인 경우 (GENERAL_CHAT 경로)
        if evidences is None:
            try:
                gemini_answer = call_llm_for_answer(question, None, user_profile)
                if gemini_answer:
                    logger.info("Gemini 일반 대화 답변 생성 성공")
                    return gemini_answer
            except Exception as e:
                logger.warning(f"Gemini 일반 대화 답변 생성 실패: {e}")
            
            # Gemini가 실패한 경우 기본 답변
            return f"안녕하세요! '{question}'에 대해 답변드리겠습니다. 더 구체적인 질문이 있으시면 언제든 말씀해주세요."
        
        # evidences가 빈 리스트인 경우, 사용자 프로필을 근거로 사용 시도
        if not evidences:
            logger.warning("검색된 근거가 없어 사용자 프로필로 대체합니다.")
            if user_profile:
                # 사용자 프로필을 evidence 형식으로 변환
                evidences = [{
                    'source': 'user_profile',
                    'doc_id': 'profile',
                    'snippet': user_profile
                }]
                # 프로필 사용에 대한 안내 문구 추가
                answer_prefix = "요청하신 내용에 대한 구체적인 정보는 찾지 못했습니다. 대신, 저장된 사용자 프로필을 바탕으로 답변해 드릴게요.\n\n"
            else:
                return "죄송합니다. 관련 정보를 찾을 수 없습니다."

        # 검색된 정보를 깔끔하게 정리
        context = _clean_search_results(evidences)
        
        if not context:
            return "관련 정보를 찾을 수 없습니다."
        
        # Gemini를 사용한 답변 생성 시도 (프로필 포함)
        try:
            gemini_answer = call_llm_for_answer(question, context, user_profile)
            if gemini_answer:
                logger.info("Gemini 답변 생성 성공")
                return answer_prefix + gemini_answer
        except Exception as e:
            logger.warning(f"Gemini 답변 생성 실패: {e}")

        # Gemini가 실패한 경우, 사용자 친화적인 오류 메시지 반환
        logger.warning("Gemini 답변 생성에 실패하여 사용자에게 오류 메시지를 반환합니다.")
        return "죄송합니다, 답변을 생성하는 데 문제가 발생했습니다. 잠시 후 다시 시도해주세요."
    except Exception as e:
        logger.error(f"응답 생성 오류: {e}")
        return "응답 생성 중 오류가 발생했습니다."


def call_vlm_for_answer(question: str, images: List[Image.Image]) -> Optional[str]:
    """VLM을 사용하여 이미지 기반 답변을 생성합니다."""
    try:
        # VLM 설정 확인
        vlm_config = _get_vlm_config()
        if not vlm_config.get('enabled', False):
            logger.debug("VLM이 비활성화되어 있습니다.")
            return None
        
        # 이미지를 base64로 변환
        images_b64 = images_to_base64(images)
        if not images_b64:
            logger.warning("이미지 base64 변환 실패")
            return None
        
        # VLM API 호출 (실제 구현은 VLM 서비스에 따라 다름)
        # 여기서는 간단한 템플릿 기반 답변 반환
        return f"이미지를 분석한 결과, {len(images)}개의 이미지에서 관련 정보를 찾았습니다. 질문: {question}"
        
    except Exception as e:
        logger.error(f"VLM 답변 생성 오류: {e}")
        return None

# ==============================================================================
# VLM 관련 설정 및 초기화
# ==============================================================================

_vlm_config = None

def _initialize_vlm_resources():
    """VLM 리소스를 초기화합니다."""
    global _vlm_config
    try:
        _vlm_config = _load_vlm_config()
        
        if _vlm_config.get('enabled', False):
            logger.info("VLM 리소스 초기화 완료")
        else:
            logger.info("VLM이 비활성화되어 있습니다.")
            
    except Exception as e:
        logger.error(f"VLM 초기화 오류: {e}")
        _vlm_config = {'enabled': False}

def _load_vlm_config(config_path: str = "configs.yaml") -> Dict[str, Any]:
    """VLM 설정을 로드합니다."""
    try:
        import yaml
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return config.get('vlm', {})
        else:
            return {
                'enabled': False,
                'api_url': '',
                'api_key': '',
                'model': 'gpt-4-vision-preview'
            }
    except Exception as e:
        logger.error(f"VLM 설정 로드 오류: {e}")
        return {'enabled': False}

def _get_vlm_config() -> Dict[str, Any]:
    """VLM 설정을 반환합니다."""
    global _vlm_config
    if _vlm_config is None:
        _initialize_vlm_resources()
    return _vlm_config or {'enabled': False}

# VLM 리소스 초기화
_initialize_vlm_resources()