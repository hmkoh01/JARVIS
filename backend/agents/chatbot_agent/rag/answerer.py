import os
import base64
import logging
from typing import List, Dict, Any, Optional
from PIL import Image
import io

logger = logging.getLogger(__name__)

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

def call_llm_for_answer(question: str, context: str, user_profile: Optional[str] = None) -> Optional[str]:
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
            
            # Gemini 모델 초기화
            genai.configure(api_key=settings.GEMINI_API_KEY)
            model = genai.GenerativeModel(settings.GEMINI_MODEL)
            
            # 시스템 컨텍스트 (사용자 프로필 포함)
            system_context = ""
            if user_profile:
                system_context = f"""
[사용자 프로필]
{user_profile}

위 프로필을 참고하여 사용자의 배경과 관심사를 고려한 맞춤형 답변을 제공하세요.
"""
            
            # 프롬프트 구성
            prompt = f"""{system_context}당신은 사용자의 질문에 대해 주어진 정보를 바탕으로 정확하고 유용한 답변을 제공하는 AI 어시스턴트입니다.

질문: {question}

참고 정보:
{context}

위 정보와 사용자 프로필을 바탕으로 질문에 답변해주세요. 다음 사항을 지켜주세요:
1. 사용자의 배경과 관심사를 고려한 맞춤형 답변을 제공하세요
2. 주어진 정보만을 사용하여 답변하세요
3. 정보가 부족한 부분은 솔직히 말하세요
4. 한국어로 자연스럽고 친근하게 답변하세요
5. 구체적이고 실용적인 정보를 제공하세요
6. 출처 정보는 간단히 언급하세요
7. 답변은 사용자의 질문에 따라 필요하다면 핵심적인 내용만 제공하세요

답변:"""

            # Gemini API 호출
            response = model.generate_content(prompt)
            
            if response and response.text:
                logger.info("Gemini 답변 생성 성공" + (" (프로필 포함)" if user_profile else ""))
                return response.text
            else:
                logger.warning("Gemini 응답이 비어있습니다.")
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

def compose_answer(question: str, evidences: List[Dict[str, Any]], user_id: Optional[int] = None) -> str:
    """검색된 근거(evidences)를 바탕으로 최종 텍스트 답변을 구성합니다."""
    try:
        if not evidences:
            return "죄송합니다. 관련 정보를 찾을 수 없습니다."

        # 검색된 정보를 컨텍스트로 정리
        context_parts = []
        for i, evidence in enumerate(evidences[:5], 1):  # 상위 5개만 사용
            snippet = evidence.get('snippet', '')
            path = evidence.get('path', '')
            url = evidence.get('url', '')
            source = evidence.get('source', 'unknown')
            
            if snippet:
                # 민감 정보 마스킹
                security_patterns = _get_security_patterns()
                clean_snippet = _redact_sensitive_info(snippet, security_patterns)
                context_parts.append(f"[정보 {i}] {clean_snippet}")
            elif path:
                context_parts.append(f"[정보 {i}] 파일: {os.path.basename(path)}")
            elif url:
                context_parts.append(f"[정보 {i}] 웹페이지: {url}")
        
        if not context_parts:
            return "관련 정보를 찾을 수 없습니다."
        
        context = "\n\n".join(context_parts)
        
        # 사용자 프로필 가져오기
        user_profile = None
        if user_id:
            try:
                from database.user_profile_indexer import UserProfileIndexer
                indexer = UserProfileIndexer()
                user_profile = indexer.get_profile_as_context(user_id)
                if user_profile:
                    logger.info(f"사용자 {user_id} 프로필을 LLM 컨텍스트에 포함")
            except Exception as e:
                logger.warning(f"프로필 로드 실패: {e}")
        
        # Gemini를 사용한 답변 생성 시도 (프로필 포함)
        try:
            gemini_answer = call_llm_for_answer(question, context, user_profile)
            if gemini_answer:
                logger.info("Gemini 답변 생성 성공")
                return gemini_answer
        except Exception as e:
            logger.warning(f"Gemini 답변 생성 실패, 기본 답변 사용: {e}")

        # Gemini가 실패한 경우 기본 답변 (기존 방식)
        security_patterns = _get_security_patterns()

        answer_parts = [f"질문: {question}\n\n", "찾은 정보:\n"]
        for i, evidence in enumerate(evidences, 1):
            source = evidence.get('source', 'unknown')
            doc_id = evidence.get('doc_id', 'unknown')
            page = evidence.get('page')
            timestamp = evidence.get('timestamp')
            snippet = evidence.get('snippet', '')
            path = evidence.get('path', '')
            url = evidence.get('url', '')

            # 근거 출처 라벨 생성
            evidence_label = f"[src:{source} doc:{doc_id}"
            if page is not None: evidence_label += f" page:{page}"
            if timestamp is not None: evidence_label += f" t:{timestamp}"
            evidence_label += "]"

            # 내용 생성 (민감 정보 마스킹 포함)
            if snippet:
                content = _redact_sensitive_info(snippet, security_patterns)
            elif path:
                content = f"파일: {os.path.basename(path)}"
            elif url:
                content = f"웹페이지: {url}"
            else:
                content = "관련 정보"
            
            answer_parts.append(f"{i}. {evidence_label} {content}\n")
        
        return "".join(answer_parts)
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