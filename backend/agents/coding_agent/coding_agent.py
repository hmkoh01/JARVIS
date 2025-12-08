"""
CodingAgent - Gemini API를 사용한 Python 코드 생성 에이전트

사용자 요청에 따라 실행 가능한 Python 코드를 생성하고 로컬 파일로 저장합니다.

사용 모델: Gemini (settings.GEMINI_MODEL)
저장 경로: ~/Documents/JARVIS/Reports/code/
"""

import re
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor

import google.generativeai as genai

from ..base_agent import BaseAgent, AgentResponse
from config.settings import settings
from utils.slugify import slugify

logger = logging.getLogger(__name__)


class CodingAgent(BaseAgent):
    """
    Python 코드 생성 에이전트
    
    Gemini API를 사용하여 사용자 요청에 맞는 실행 가능한 Python 코드를 생성하고,
    로컬 파일로 저장합니다.
    
    Features:
        - Gemini API 기반 코드 생성
        - 첨부 파일 컨텍스트 활용
        - Qdrant 벡터 검색 (코드 관련 문서)
        - 자동 파일 저장 및 메타데이터 반환
    """
    
    def __init__(self):
        super().__init__(
            agent_type="coding",
            description="코드 작성, 디버깅, 프로그래밍 관련 질문을 처리합니다."
        )
        self._init_llm()
        self._init_code_dir()
    
    def _init_llm(self):
        """Gemini LLM 클라이언트 초기화"""
        self.llm_available = False
        
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY가 설정되지 않아 CodingAgent LLM 기능을 사용할 수 없습니다.")
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
            
            # 코드 생성용 모델
            self.llm_model = genai.GenerativeModel(
                model_name=settings.GEMINI_MODEL,
                generation_config={
                    "temperature": 0.3,  # 코드 생성은 낮은 temperature
                    "top_p": 0.95,
                    "top_k": 40,
                    "max_output_tokens": 8192,
                    "response_mime_type": "text/plain",
                },
                safety_settings=self.safety_settings,
            )
            
            self.llm_available = True
            logger.info(f"CodingAgent: Gemini LLM 클라이언트 초기화 완료 (model={settings.GEMINI_MODEL})")
            
        except Exception as e:
            logger.error(f"CodingAgent: Gemini LLM 초기화 오류: {e}")
    
    def _init_code_dir(self):
        """코드 저장 디렉터리 초기화"""
        import os
        
        # ~/Documents/JARVIS/code/ 경로 사용
        home = os.path.expanduser("~")
        jarvis_dir = os.path.join(home, "Documents", "JARVIS")
        self.code_dir = Path(jarvis_dir) / "code"
        
        try:
            self.code_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"CodingAgent: 코드 저장 경로: {self.code_dir}")
        except Exception as e:
            logger.error(f"CodingAgent: 코드 디렉터리 생성 실패: {e}")
            # Fallback to temp directory
            import tempfile
            self.code_dir = Path(tempfile.gettempdir()) / "JARVIS_Code"
            self.code_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"CodingAgent: Fallback 코드 저장 경로: {self.code_dir}")
    
    # ============================================================
    # BaseAgent Interface Implementation
    # ============================================================
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        동기 처리 메서드 (langgraph 호환)
        
        Args:
            state: 상태 딕셔너리
                - question: 사용자 질문/요청
                - user_id: 사용자 ID
                - attached_files: 첨부 파일 목록 (선택)
                - chat_history: 대화 기록 (선택)
        
        Returns:
            처리된 상태 딕셔너리
        """
        question = state.get("question", "")
        user_id = state.get("user_id")
        attached_files = state.get("attached_files", [])
        chat_history = state.get("chat_history", [])
        
        if not question:
            return {
                **state,
                "answer": "코드 생성 요청이 제공되지 않았습니다.",
                "success": False,
                "agent_type": self.agent_type
            }
        
        # 비동기 함수를 동기적으로 실행
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 이벤트 루프가 실행 중이면 새 태스크 생성
                with ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._generate_code(question, user_id, attached_files, chat_history)
                    )
                    result = future.result(timeout=120)
            else:
                result = loop.run_until_complete(
                    self._generate_code(question, user_id, attached_files, chat_history)
                )
        except Exception as e:
            logger.exception(f"CodingAgent process 오류: {e}")
            result = {
                "success": False,
                "answer": f"코드 생성 중 오류가 발생했습니다: {str(e)}",
                "metadata": {}
            }
        
        return {
            **state,
            "answer": result.get("answer", "코드 생성에 실패했습니다."),
            "success": result.get("success", False),
            "agent_type": self.agent_type,
            "metadata": result.get("metadata", {})
        }
    
    # ============================================================
    # Main Code Generation Logic
    # ============================================================
    
    async def _generate_code(
        self,
        question: str,
        user_id: Optional[int],
        attached_files: List[Dict[str, Any]],
        chat_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        코드 생성 메인 메서드
        
        Args:
            question: 사용자 요청
            user_id: 사용자 ID
            attached_files: 첨부 파일 목록
            chat_history: 대화 기록
        
        Returns:
            {
                "success": bool,
                "answer": str,
                "metadata": {
                    "file_path": str,
                    "file_name": str,
                    "action": "open_file",
                    "code_preview": str
                }
            }
        """
        logger.info(f"CodingAgent: 코드 생성 시작 - user_id={user_id}, question='{question[:50]}...'")
        
        try:
            if not self.llm_available:
                return {
                    "success": False,
                    "answer": "LLM 서비스를 사용할 수 없습니다. GEMINI_API_KEY를 확인해주세요.",
                    "metadata": {}
                }
            
            # Step 1: 컨텍스트 구성 (첨부 파일 + Qdrant 검색)
            context = await self._build_context(question, user_id, attached_files)
            
            # Step 2: 프롬프트 구성 및 LLM 호출
            generated_code, explanation = await self._call_llm_for_code(
                question, context, chat_history
            )
            
            if not generated_code:
                return {
                    "success": False,
                    "answer": "코드를 생성할 수 없습니다. 요청을 더 구체적으로 작성해주세요.",
                    "metadata": {}
                }
            
            # Step 3: 파일 저장
            file_path, file_name = await self._save_code_to_file(question, generated_code)
            
            # Step 4: 코드 프리뷰 생성 (처음 20줄)
            code_lines = generated_code.split('\n')
            code_preview = '\n'.join(code_lines[:20])
            if len(code_lines) > 20:
                code_preview += f"\n... ({len(code_lines) - 20}줄 더 있음)"
            
            # Step 5: 응답 구성
            answer = self._format_response(question, explanation, file_name, generated_code)
            
            logger.info(f"CodingAgent: 코드 생성 완료 - {file_path}")
            
            return {
                "success": True,
                "answer": answer,
                "metadata": {
                    "file_path": str(file_path),
                    "file_name": file_name,
                    "action": "open_file",
                    "code_preview": code_preview
                }
            }
            
        except Exception as e:
            logger.exception(f"CodingAgent: 코드 생성 중 오류: {e}")
            return {
                "success": False,
                "answer": f"코드 생성 중 오류가 발생했습니다: {str(e)}",
                "metadata": {}
            }
    
    # ============================================================
    # Context Building
    # ============================================================
    
    async def _build_context(
        self,
        question: str,
        user_id: Optional[int],
        attached_files: List[Dict[str, Any]]
    ) -> str:
        """
        LLM에 제공할 컨텍스트를 구성합니다.
        
        Args:
            question: 사용자 질문
            user_id: 사용자 ID
            attached_files: 첨부 파일 목록
        
        Returns:
            컨텍스트 문자열
        """
        context_parts = []
        
        # 1. 첨부 파일 내용 읽기
        if attached_files:
            file_contents = await self._read_attached_files(attached_files)
            if file_contents:
                context_parts.append("## 첨부된 파일 내용\n")
                for file_info in file_contents:
                    context_parts.append(f"### {file_info['name']}\n```\n{file_info['content']}\n```\n")
        
        # 2. Qdrant 검색 (코드 관련 문서)
        qdrant_results = await self._search_qdrant(question, user_id)
        if qdrant_results:
            context_parts.append("## 관련 참고 자료\n")
            for i, result in enumerate(qdrant_results, 1):
                context_parts.append(f"### 참고 {i}\n{result}\n")
        
        return "\n".join(context_parts) if context_parts else ""
    
    async def _read_attached_files(
        self,
        attached_files: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """
        첨부 파일 내용을 읽습니다.
        
        Args:
            attached_files: 첨부 파일 정보 목록
                - path: 파일 경로
                - name: 파일명
        
        Returns:
            파일 내용 목록
        """
        file_contents = []
        
        for file_info in attached_files:
            try:
                file_path = file_info.get("path") or file_info.get("file_path")
                file_name = file_info.get("name") or file_info.get("file_name") or "unknown"
                
                if not file_path:
                    continue
                
                path = Path(file_path)
                if not path.exists():
                    logger.warning(f"CodingAgent: 첨부 파일을 찾을 수 없음: {file_path}")
                    continue
                
                # 텍스트 파일만 읽기
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # 내용이 너무 길면 자르기
                    if len(content) > 5000:
                        content = content[:5000] + "\n... (파일 내용이 너무 길어 일부만 표시)"
                    
                    file_contents.append({
                        "name": file_name,
                        "content": content
                    })
                    logger.debug(f"CodingAgent: 첨부 파일 읽기 성공: {file_name}")
                    
                except UnicodeDecodeError:
                    logger.warning(f"CodingAgent: 텍스트로 읽을 수 없는 파일: {file_name}")
                    
            except Exception as e:
                logger.warning(f"CodingAgent: 첨부 파일 읽기 오류: {e}")
        
        return file_contents
    
    async def _search_qdrant(
        self,
        question: str,
        user_id: Optional[int]
    ) -> List[str]:
        """
        Qdrant에서 코드 관련 문서를 검색합니다.
        
        Args:
            question: 검색 질문
            user_id: 사용자 ID (필터링용)
        
        Returns:
            검색 결과 문자열 목록
        """
        try:
            from database.qdrant_client import QdrantManager
            from agents.chatbot_agent.rag.models.bge_m3_embedder import BGEM3Embedder
            
            # Embedder 및 Qdrant 클라이언트 초기화
            embedder = BGEM3Embedder()
            qdrant = QdrantManager()
            
            # 질문 임베딩 (encode_queries 메서드 사용)
            embeddings = embedder.encode_queries([question])
            if not embeddings or 'dense' not in embeddings:
                return []
            
            query_dense = embeddings['dense'][0].tolist() if hasattr(embeddings['dense'][0], 'tolist') else list(embeddings['dense'][0])
            
            # sparse 벡터 처리
            query_sparse = {'indices': [], 'values': []}
            if 'sparse' in embeddings and len(embeddings['sparse']) > 0:
                sparse_data = embeddings['sparse'][0]
                if hasattr(sparse_data, 'indices') and hasattr(sparse_data, 'values'):
                    query_sparse = {
                        'indices': sparse_data.indices.tolist() if hasattr(sparse_data.indices, 'tolist') else list(sparse_data.indices),
                        'values': sparse_data.values.tolist() if hasattr(sparse_data.values, 'tolist') else list(sparse_data.values)
                    }
            
            # 필터 구성
            query_filter = {}
            if user_id:
                query_filter['user_id'] = user_id
            
            # 하이브리드 검색
            results = qdrant.hybrid_search(
                query_dense=query_dense,
                query_sparse=query_sparse,
                limit=5,
                query_filter=query_filter if query_filter else None
            )
            
            # 결과 추출
            context_texts = []
            for result in results:
                payload = result.get('payload', {})
                text = payload.get('text') or payload.get('content', '')
                if text:
                    # 내용이 너무 길면 자르기
                    if len(text) > 1000:
                        text = text[:1000] + "..."
                    context_texts.append(text)
            
            logger.debug(f"CodingAgent: Qdrant 검색 결과 {len(context_texts)}개")
            return context_texts
            
        except ImportError as e:
            logger.warning(f"CodingAgent: Qdrant 모듈을 찾을 수 없음: {e}")
            return []
        except Exception as e:
            logger.warning(f"CodingAgent: Qdrant 검색 오류: {e}")
            return []
    
    # ============================================================
    # LLM Code Generation
    # ============================================================
    
    async def _call_llm_for_code(
        self,
        question: str,
        context: str,
        chat_history: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], str]:
        """
        LLM을 호출하여 코드를 생성합니다.
        
        Args:
            question: 사용자 요청
            context: 참고 컨텍스트
            chat_history: 대화 기록
        
        Returns:
            (생성된 코드, 설명)
        """
        # 대화 기록 포맷팅
        history_text = ""
        if chat_history:
            history_parts = []
            for msg in chat_history[-5:]:  # 최근 5개 메시지만
                role = msg.get("role", "user")
                content = msg.get("content", "")
                history_parts.append(f"{role}: {content}")
            history_text = "\n".join(history_parts)
        
        prompt = f"""당신은 숙련된 Python 전문 개발자입니다. 
사용자의 요청에 따라 실행 가능한 Python 코드를 작성해주세요.

## 코드 작성 규칙
1. **실행 가능**: 코드는 바로 실행할 수 있어야 합니다.
2. **import 포함**: 필요한 모든 import 문을 코드 상단에 포함하세요.
3. **에러 처리**: 적절한 try-except 블록으로 에러를 처리하세요.
4. **주석 포함**: 코드의 주요 부분에 한국어 주석을 달아주세요.
5. **메인 함수**: 가능하면 `if __name__ == "__main__":` 블록을 포함하세요.
6. **타입 힌트**: 함수에 타입 힌트를 사용하세요.
7. **PEP 8 준수**: Python 스타일 가이드를 준수하세요.

{f"## 참고 컨텍스트{chr(10)}{context}" if context else ""}

{f"## 이전 대화{chr(10)}{history_text}" if history_text else ""}

## 사용자 요청
{question}

## 응답 형식
먼저 코드에 대한 간단한 설명을 작성하고, 그 다음 ```python 코드 블록으로 전체 코드를 작성하세요.
코드 블록 안에는 실행 가능한 완전한 Python 코드만 포함하세요.
"""

        try:
            response = self.llm_model.generate_content(
                prompt,
                request_options={"timeout": 60}
            )
            
            response_text = self._extract_llm_response(response)
            if not response_text:
                return None, ""
            
            # 코드 블록 추출
            generated_code = self._extract_code_block(response_text)
            
            # 설명 추출 (코드 블록 이전 텍스트)
            explanation = self._extract_explanation(response_text)
            
            return generated_code, explanation
            
        except Exception as e:
            logger.error(f"CodingAgent: LLM 호출 오류: {e}")
            return None, ""
    
    def _extract_code_block(self, text: str) -> Optional[str]:
        """
        텍스트에서 Python 코드 블록을 추출합니다.
        
        Args:
            text: LLM 응답 텍스트
        
        Returns:
            추출된 코드 또는 None
        """
        # ```python 또는 ```py 코드 블록 찾기
        pattern = r'```(?:python|py)\n(.*?)```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            # 가장 긴 코드 블록 선택 (보통 메인 코드)
            return max(matches, key=len).strip()
        
        # 일반 ``` 코드 블록 찾기
        pattern = r'```\n(.*?)```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            return max(matches, key=len).strip()
        
        return None
    
    def _extract_explanation(self, text: str) -> str:
        """
        텍스트에서 코드 설명 부분을 추출합니다.
        
        Args:
            text: LLM 응답 텍스트
        
        Returns:
            설명 텍스트
        """
        # 첫 번째 코드 블록 이전의 텍스트
        code_start = text.find('```')
        if code_start > 0:
            explanation = text[:code_start].strip()
            # 너무 긴 설명은 자르기
            if len(explanation) > 500:
                explanation = explanation[:500] + "..."
            return explanation
        
        return ""
    
    # ============================================================
    # File Saving
    # ============================================================
    
    async def _save_code_to_file(
        self,
        question: str,
        code: str
    ) -> Tuple[str, str]:
        """
        생성된 코드를 파일로 저장합니다.
        
        Args:
            question: 원본 질문 (파일명 생성용)
            code: 저장할 코드
        
        Returns:
            (파일 경로, 파일명)
        """
        # 키워드 추출 (간단한 방식)
        keyword = self._extract_keyword_from_question(question)
        
        # 타임스탬프 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 파일명 생성
        keyword_slug = slugify(keyword, max_length=30)
        file_name = f"{keyword_slug}_{timestamp}.py"
        file_path = self.code_dir / file_name
        
        # 파일 저장
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                # 파일 헤더 추가
                header = f'''"""
자동 생성된 코드
생성 일시: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
요청: {question[:100]}{'...' if len(question) > 100 else ''}

이 코드는 JARVIS CodingAgent에 의해 자동 생성되었습니다.
"""

'''
                f.write(header + code)
            
            logger.info(f"CodingAgent: 코드 파일 저장 완료: {file_path}")
            return str(file_path), file_name
            
        except Exception as e:
            logger.error(f"CodingAgent: 파일 저장 오류: {e}")
            raise
    
    def _extract_keyword_from_question(self, question: str) -> str:
        """
        질문에서 핵심 키워드를 추출합니다.
        
        Args:
            question: 사용자 질문
        
        Returns:
            추출된 키워드
        """
        # 불필요한 단어 제거
        stopwords = [
            "코드", "작성", "해줘", "해주세요", "만들어", "만들어줘", "만들어주세요",
            "프로그램", "스크립트", "파이썬", "python", "작성해", "생성해",
            "을", "를", "이", "가", "은", "는", "에", "의", "로", "으로",
            "좀", "하나", "간단한", "간단히", "쉽게", "빠르게"
        ]
        
        # 단어 분리 및 필터링
        words = question.split()
        keywords = [w for w in words if w.lower() not in stopwords and len(w) > 1]
        
        if keywords:
            # 처음 3개 단어 사용
            return "_".join(keywords[:3])
        
        return "generated_code"
    
    # ============================================================
    # Response Formatting
    # ============================================================
    
    def _format_response(
        self,
        question: str,
        explanation: str,
        file_name: str,
        code: str
    ) -> str:
        """
        최종 응답을 포맷팅합니다.
        
        Args:
            question: 원본 사용자 요청
            explanation: 코드 설명
            file_name: 저장된 파일명
            code: 생성된 코드
        
        Returns:
            포맷팅된 응답 문자열
        """
        # 요청 요약 (최대 50자)
        request_summary = question[:50] + "..." if len(question) > 50 else question
        
        response_parts = []
        response_parts.append(f"✅ **'{request_summary}'에 대한 코드 작성을 완료했습니다!**\n")
        
        if explanation:
            response_parts.append(f"📝 **코드 설명:**\n{explanation}\n")
        
        response_parts.append(f"💾 **저장된 파일:** `{file_name}`\n")
        response_parts.append("아래 버튼을 눌러 코드 파일을 확인하세요.")
        
        return "\n".join(response_parts)
    
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
            logger.error(f"CodingAgent: LLM 응답 추출 오류: {e}")
            return None
