import os
import sys
import asyncio
from pathlib import Path

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from typing import Dict, Any, Optional, List, Annotated, Sequence, TypedDict, TYPE_CHECKING, Generator, Union
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage
from langgraph.graph import StateGraph
from langgraph.constants import START, END
from langgraph.graph.message import add_messages
import google.generativeai as genai
import logging
import threading
from config.settings import settings
from core.agent_registry import agent_registry
from agents.base_agent import AgentResponse

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agents.base_agent import BaseAgent
else:
    BaseAgent = None


class UserIntent(BaseModel):
    """사용자 의도를 나타내는 모델"""
    user_id: Optional[int] = None
    message: str
    context: Dict[str, Any] = {}


class SupervisorResponse(BaseModel):
    """Supervisor 응답을 나타내는 모델"""
    success: bool
    selected_agent: str
    response: AgentResponse
    reasoning: str
    metadata: Dict[str, Any] = {}
    stream_generator: Optional[Any] = None  # 스트리밍 generator


class AgentState(TypedDict):
    """LangGraph 상태 정의"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_input: str
    user_id: Optional[int]
    user_context: Dict[str, Any]
    selected_agents: List[str]
    execution_mode: str  # "sequential" | "parallel"
    reasoning: str
    agent_responses: List[Dict[str, Any]]
    final_response: str
    agent_success: bool
    agent_type: str
    agent_metadata: Dict[str, Any]
    available_agents: List[str]
    stream: bool  # 스트리밍 여부


class LangGraphSupervisor:
    """LangGraph 기반 Supervisor - 사용자 의도를 분석하고 적절한 에이전트를 선택하는 그래프 워크플로우"""
    
    def __init__(self):
        self.llm = self._initialize_llm()
        self.agent_descriptions = agent_registry.get_agent_descriptions()
        self.graph = self._create_agent_graph()
    
    def _initialize_llm(self):
        """LLM을 초기화합니다."""
        try:
            logger.debug(
                f"GEMINI_API_KEY 확인: {settings.GEMINI_API_KEY[:10]}..."
                if settings.GEMINI_API_KEY
                else "GEMINI_API_KEY 없음"
            )
            
            if settings.GEMINI_API_KEY:
                logger.info("Gemini API 초기화 시도...")
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel(settings.GEMINI_MODEL)
                logger.info("Gemini API 초기화 성공")
                return model
            else:
                logger.error("GEMINI_API_KEY가 설정되지 않았습니다. LLM 의도 분석을 사용할 수 없습니다.")
                return None
        except Exception as e:
            logger.error(f"LLM 초기화 오류: {e}")
            return None
    
    def _create_agent_graph(self) -> StateGraph:
        """에이전트 선택 및 실행 그래프를 생성합니다."""
        workflow = StateGraph(AgentState)
        
        # 노드 추가
        workflow.add_node("intent_analyzer", self._intent_analyzer_node)
        workflow.add_node("agent_selector", self._agent_selector_node)
        workflow.add_node("agent_executor", self._agent_executor_node)
        
        # 엣지 연결
        workflow.add_edge(START, "intent_analyzer")
        workflow.add_edge("intent_analyzer", "agent_selector")
        workflow.add_edge("agent_selector", "agent_executor")
        workflow.add_edge("agent_executor", END)
        
        return workflow.compile()
    
    def visualize_graph(self) -> str:
        """LangGraph 워크플로우를 시각화합니다."""
        try:
            return self.graph.get_graph().draw_mermaid()
        except Exception as e:
            return f"그래프 시각화 중 오류: {str(e)}"
    
    def get_graph_info(self) -> Dict[str, Any]:
        """그래프 정보를 반환합니다."""
        try:
            graph = self.graph.get_graph()
            return {
                "nodes": list(graph.nodes.keys()),
                "edges": [(edge.source, edge.target) for edge in graph.edges],
                "total_nodes": len(graph.nodes),
                "total_edges": len(graph.edges),
                "framework": "LangGraph"
            }
        except Exception as e:
            return {"error": f"그래프 정보 조회 중 오류: {str(e)}"}
    
    async def _intent_analyzer_node(self, state: AgentState) -> AgentState:
        """사용자 의도를 분석하는 노드 (LLM 전용)"""
        try:
            user_input = state["user_input"]
            user_id = state.get("user_id")
            
            logger.info("=" * 70)
            logger.info("[MAS] 의도 분석 시작 | user_id=%s | message='%s'", 
                user_id, user_input[:80] + "..." if len(user_input) > 80 else user_input)
            
            # LLM을 사용한 의도 분석
            intent_analysis = await self._analyze_intent_with_llm(user_input)
            
            selected_agents = intent_analysis.get("selected_agents", ["chatbot"])
            execution_mode = intent_analysis.get("execution_mode", "sequential")
            is_multi_agent = len(selected_agents) > 1
            
            logger.info("[MAS] 의도 분석 완료")
            logger.info("  ├─ 선택된 에이전트: %s %s", selected_agents, "(복합)" if is_multi_agent else "")
            logger.info("  ├─ 실행 모드: %s", execution_mode.upper())
            logger.info("  ├─ 신뢰도: %.0f%%", intent_analysis.get("confidence", 0) * 100)
            logger.info("  └─ 의도: %s", intent_analysis.get("intent", ""))
            
            new_state = state.copy()
            new_state["reasoning"] = intent_analysis.get("reasoning", "")
            new_state["selected_agents"] = selected_agents
            new_state["execution_mode"] = execution_mode
            new_state["intent_analysis"] = intent_analysis
            
            return new_state
        except Exception as e:
            logger.error("[MAS] 의도 분석 실패: %s", e, exc_info=True)
            new_state = state.copy()
            new_state["reasoning"] = f"의도 분석 중 오류가 발생했습니다: {str(e)}"
            new_state["selected_agents"] = ["chatbot"]
            new_state["execution_mode"] = "sequential"
            new_state["agent_success"] = False
            return new_state

    async def _analyze_intent_with_llm(self, user_input: str) -> Dict[str, Any]:
        """LLM을 사용하여 사용자 의도를 분석합니다 (LLM 전용, 폴백 없음)."""
        # LLM이 초기화되지 않은 경우 에러 반환
        if self.llm is None:
            raise RuntimeError("LLM이 초기화되지 않았습니다. GEMINI_API_KEY를 확인해주세요.")
        
        try:
            # LLM 프롬프트 생성
            prompt = self._create_llm_intent_prompt(user_input)
            
            if hasattr(self.llm, 'generate_content'):
                # Gemini API 사용
                response = self.llm.generate_content(prompt)
                analysis_text = response.text
            else:
                # LangChain 모델 사용
                messages = [HumanMessage(content=prompt)]
                response = await self.llm.ainvoke(messages)
                analysis_text = response.content
            
            # LLM 응답 파싱
            parsed_analysis = self._parse_llm_response(analysis_text)
            
            if not parsed_analysis or not parsed_analysis.get("selected_agents"):
                raise ValueError("LLM 응답 파싱 실패: 유효한 에이전트 선택 결과가 없습니다.")
            
            return parsed_analysis
            
        except Exception as e:
            logger.error(f"LLM 의도 분석 오류: {e}", exc_info=True)
            raise

    # ------------------------------------------------------------
    # Helper: intro_text 축약
    # ------------------------------------------------------------
    def _shorten_intro_text(self, text: str) -> str:
        """intro_text가 너무 길지 않도록 1문장, 최대 120자 이내로 축약합니다."""
        if not text:
            return ""
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        first_sentence = sentences[0].strip() if sentences else text.strip()
        if len(first_sentence) > 120:
            return first_sentence[:117] + "..."
        return first_sentence

    def _create_llm_intent_prompt(self, user_input: str) -> str:
        """LLM 의도 분석을 위한 프롬프트를 생성합니다."""
        agent_descriptions = "\n".join([
            f"- {agent_type}: {description}"
            for agent_type, description in self.agent_descriptions.items()
        ])
        
        return f"""당신은 사용자의 의도를 분석하여 적절한 AI 에이전트를 선택하는 전문가입니다.

사용 가능한 에이전트들:
{agent_descriptions}

사용자의 메시지를 분석하여 어떤 에이전트가 가장 적합한지 판단하세요.
복잡한 요청의 경우 여러 에이전트를 조합하여 사용할 수 있습니다.

분석 결과는 다음과 같은 JSON 형식으로 제공해주세요:
{{
    "selected_agents": ["에이전트1", "에이전트2"],
    "sub_tasks": {{
        "에이전트1": {{"task": "에이전트1이 수행할 구체적인 태스크", "focus": "집중할 부분"}},
        "에이전트2": {{"task": "에이전트2가 수행할 구체적인 태스크", "focus": "집중할 부분"}}
    }},
    "primary_agent": "주요 에이전트명",
    "execution_mode": "sequential" | "parallel",
    "confidence": 0.95,
    "reasoning": "선택 이유와 에이전트 조합 이유 설명",
    "keywords": ["키워드1", "키워드2"],
    "intent": "사용자 의도 요약",
    "intro_text": "사용자에게 바로 보여줄 친근한 서론 메시지 (1-2문장)"
}}

intro_text 작성 기준:
- 사용자의 요청을 이해했음을 알리는 친근한 메시지
- 무엇을 할 것인지 간단히 예고 (예: "~에 대해 알아보고 코드도 작성해드릴게요!")
- 반말로 친근하게 작성 (예: "~하시는군요!", "~해드릴게요!")
- 1-2문장으로 간결하게

sub_tasks 작성 기준:
- 각 에이전트가 수행할 구체적인 태스크를 분리하여 작성
- 복합 요청의 경우 사용자 메시지에서 각 에이전트에 해당하는 부분을 추출
- task: 해당 에이전트가 처리할 구체적인 요청 (한 문장)
- focus: 특별히 집중해야 할 부분이나 키워드

에이전트 선택 기준:
- coding: 코드 작성, 디버깅, 프로그래밍 관련 질문
- dashboard: 사용자 데이터 분석, 관심사 트렌드 분석, 활동 패턴 분석, 기간 비교, 통계 분석 요청
- recommendation: 추천, 제안 요청
- report: 보고서 생성, 리포트 작성, 특정 주제에 대한 심층 보고서 요청
- chatbot: 일반적인 질문, 대화, 이미지 분석, 멀티모달 질문

execution_mode 결정 기준:
- "sequential": 에이전트들이 순서대로 실행되어야 할 때 (앞 에이전트 결과가 뒤 에이전트에 필요한 경우)
  예: "코드를 작성하고 그 결과를 분석해줘" → coding → dashboard (순차)
- "parallel": 에이전트들이 독립적으로 동시에 실행 가능할 때
  예: "날씨 알려주고 추천도 해줘" → chatbot, recommendation (병렬)

복합 요청 예시:
- "코드를 작성하고 대시보드로 시각화해줘" → ["coding", "dashboard"], "sequential"
- "추천해주고 분석 결과도 보여줘" → ["recommendation", "dashboard"], "parallel"
- "이미지를 분석하고 코드로 구현해줘" → ["chatbot", "coding"], "sequential"

단일 요청 예시:
- "내 활동 분석해줘" → ["dashboard"], "sequential"
- "Docker에 대한 보고서를 작성해줘" → ["report"], "sequential"
- "안녕하세요" → ["chatbot"], "sequential"

사용자 메시지: {user_input}

JSON 응답만 제공해주세요:"""

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """LLM 응답을 파싱합니다."""
        try:
            import json
            import re
            
            # JSON 부분 추출
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                parsed = json.loads(json_str)
                
                # 필수 필드 검증 및 기본값 설정
                if "selected_agents" not in parsed or not parsed["selected_agents"]:
                    parsed["selected_agents"] = ["chatbot"]
                if "primary_agent" not in parsed:
                    parsed["primary_agent"] = parsed["selected_agents"][0]
                if "execution_mode" not in parsed:
                    parsed["execution_mode"] = "sequential"
                if "reasoning" not in parsed:
                    parsed["reasoning"] = "LLM 분석 결과"
                if "confidence" not in parsed:
                    parsed["confidence"] = 0.8
                if "keywords" not in parsed:
                    parsed["keywords"] = []
                if "intent" not in parsed:
                    parsed["intent"] = "사용자 의도 분석"
                if "sub_tasks" not in parsed:
                    parsed["sub_tasks"] = {}
                
                return parsed
            else:
                logger.error(f"LLM 응답에서 JSON을 찾을 수 없음: {response_text[:200]}")
                return {}
                
        except Exception as e:
            logger.error(f"LLM 응답 파싱 오류: {e}", exc_info=True)
            return {}

    async def _agent_selector_node(self, state: AgentState) -> AgentState:
        """적절한 에이전트를 선택하는 노드"""
        try:
            selected_agents = state.get("selected_agents", ["chatbot"])
            
            new_state = state.copy()
            new_state["selected_agents"] = selected_agents
            new_state["available_agents"] = list(self.agent_descriptions.keys())
            return new_state
            
        except Exception as e:
            new_state = state.copy()
            new_state["selected_agents"] = ["chatbot"]
            new_state["reasoning"] += f" 에이전트 선택 중 오류: {str(e)}"
            return new_state
    
    async def _agent_executor_node(self, state: AgentState) -> AgentState:
        """선택된 에이전트를 실행하는 노드 (순차/병렬 스케줄링 지원)"""
        import time
        try:
            selected_agents = state["selected_agents"]
            user_input = state["user_input"]
            user_id = state["user_id"]
            execution_mode = state.get("execution_mode", "sequential")
            is_multi_agent = len(selected_agents) > 1
            
            logger.info("-" * 70)
            logger.info("[MAS] 에이전트 실행 시작 | %s | %d개 에이전트", 
                execution_mode.upper(), len(selected_agents))
            if is_multi_agent:
                logger.info("  └─ 에이전트 목록: %s", " → ".join(selected_agents) if execution_mode == "sequential" else " + ".join(selected_agents))
            
            agent_responses = []
            final_response = ""
            agent_success = True
            primary_agent_type = "unknown"
            
            start_time = time.time()
            
            if execution_mode == "parallel" and len(selected_agents) > 1:
                # 병렬 실행
                logger.info("[MAS] 병렬 실행 시작...")
                agent_responses, agent_success, primary_agent_type = await self._execute_agents_parallel(
                    selected_agents, user_input, user_id, state
                )
            else:
                # 순차 실행
                logger.info("[MAS] 순차 실행 시작...")
                agent_responses, agent_success, primary_agent_type = await self._execute_agents_sequential(
                    selected_agents, user_input, user_id, state
                )
            
            elapsed_time = time.time() - start_time
            
            # 실행 결과 로깅
            successful_agents = [resp["agent_type"] for resp in agent_responses if resp["success"]]
            failed_agents = [resp["agent_type"] for resp in agent_responses if not resp["success"]]
            
            logger.info("[MAS] 에이전트 실행 완료 | %.2f초", elapsed_time)
            logger.info("  ├─ 성공: %s", successful_agents if successful_agents else "없음")
            if failed_agents:
                logger.info("  ├─ 실패: %s", failed_agents)
            
            # 각 에이전트 응답 요약
            for resp in agent_responses:
                content = resp.get("content", "") or ""
                preview = content[:60].replace("\n", " ")
                status = "✓" if resp["success"] else "✗"
                logger.info("  │  [%s] %s: '%s%s'", 
                    status, resp["agent_type"], preview, "..." if len(content) > 60 else "")
            
            # 최종 응답 설정
            if agent_responses:
                if len(agent_responses) == 1:
                    final_response = agent_responses[0].get("content", "")
                else:
                    logger.info("  └─ 응답 통합 중...")
                    final_response = self._combine_agent_responses(agent_responses, user_input)
            
            logger.info("=" * 70)
            
            new_state = state.copy()
            new_state["agent_responses"] = agent_responses
            new_state["final_response"] = final_response
            new_state["agent_success"] = agent_success
            new_state["agent_type"] = primary_agent_type
            new_state["agent_metadata"] = {
                "total_agents": len(selected_agents),
                "execution_mode": execution_mode,
                "executed_agents": [resp["agent_type"] for resp in agent_responses],
                "successful_agents": successful_agents,
                "failed_agents": failed_agents,
                "elapsed_time": elapsed_time
            }
            
            return new_state
                
        except Exception as e:
            logger.error("[MAS] 에이전트 실행 실패: %s", e, exc_info=True)
            new_state = state.copy()
            new_state["agent_responses"] = []
            new_state["final_response"] = f"에이전트 실행 중 오류: {str(e)}"
            new_state["agent_success"] = False
            new_state["agent_type"] = "unknown"
            new_state["agent_metadata"] = {"error": str(e)}
            return new_state
    
    async def _execute_agents_sequential(
        self, 
        selected_agents: List[str], 
        user_input: str, 
        user_id: Optional[int],
        state: AgentState
    ) -> tuple:
        """에이전트를 순차적으로 실행합니다."""
        agent_responses = []
        agent_success = True
        primary_agent_type = "unknown"
        
        for i, agent_type in enumerate(selected_agents):
            response = await self._execute_single_agent(agent_type, user_input, user_id, state, i)
            agent_responses.append(response)
            
            if i == 0:
                primary_agent_type = agent_type
            
            if not response["success"]:
                agent_success = False
        
        return agent_responses, agent_success, primary_agent_type
    
    async def _execute_agents_parallel(
        self, 
        selected_agents: List[str], 
        user_input: str, 
        user_id: Optional[int],
        state: AgentState
    ) -> tuple:
        """에이전트를 병렬로 실행합니다."""
        agent_success = True
        primary_agent_type = selected_agents[0] if selected_agents else "unknown"
        
        # 병렬 실행을 위한 태스크 생성
        tasks = [
            self._execute_single_agent(agent_type, user_input, user_id, state, i)
            for i, agent_type in enumerate(selected_agents)
        ]
        
        # 모든 태스크 병렬 실행
        agent_responses = await asyncio.gather(*tasks)
        
        # 결과를 리스트로 변환하고 성공 여부 확인
        agent_responses = list(agent_responses)
        for resp in agent_responses:
            if not resp["success"]:
                agent_success = False
        
        return agent_responses, agent_success, primary_agent_type
    
    async def _execute_single_agent(
        self,
        agent_type: str,
        user_input: str,
        user_id: Optional[int],
        state: AgentState,
        order: int
    ) -> Dict[str, Any]:
        """단일 에이전트를 실행합니다."""
        import time
        start_time = time.time()
        
        try:
            agent = agent_registry.get_agent(agent_type)
            
            if not agent:
                logger.warning("[MAS]   └─ [%d] %s: 에이전트를 찾을 수 없음", order + 1, agent_type)
                return {
                    "agent_type": agent_type,
                    "content": f"에이전트를 찾을 수 없습니다: {agent_type}",
                    "success": False,
                    "metadata": {},
                    "evidence": [],
                    "order": order + 1
                }
            
            logger.debug("[MAS]   ├─ [%d] %s 실행 중...", order + 1, agent_type)
            
            # process(state) 패턴으로 에이전트 실행
            agent_state = {
                "question": user_input,
                "user_id": user_id,
                "session_id": state.get("user_context", {}).get("session_id"),
                "filters": state.get("user_context", {}).get("filters", {}),
                "time_hint": state.get("user_context", {}).get("time_hint"),
                "context": state.get("user_context", {})
            }
            
            result_state = agent.process(agent_state)
            
            elapsed = time.time() - start_time
            success = result_state.get("success", True)
            
            logger.debug("[MAS]   └─ [%d] %s 완료 (%.2f초, %s)", 
                order + 1, agent_type, elapsed, "성공" if success else "실패")
            
            return {
                "agent_type": agent_type,
                "content": result_state.get("answer", ""),
                "success": success,
                "metadata": result_state.get("metadata", {}),
                "evidence": result_state.get("evidence", []),
                "order": order + 1,
                "elapsed_time": elapsed
            }
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("[MAS]   └─ [%d] %s 오류 (%.2f초): %s", 
                order + 1, agent_type, elapsed, e)
            return {
                "agent_type": agent_type,
                "content": f"에이전트 실행 중 오류: {str(e)}",
                "success": False,
                "metadata": {"error": str(e)},
                "evidence": [],
                "order": order + 1
            }

    def _combine_agent_responses(self, agent_responses: List[Dict[str, Any]], user_input: str) -> str:
        """여러 에이전트의 응답을 통합합니다."""
        try:
            if not agent_responses:
                return "에이전트 응답이 없습니다."
            
            if len(agent_responses) == 1:
                return agent_responses[0]["content"]
            
            successful_responses = [resp for resp in agent_responses if resp["success"]]
            
            if not successful_responses:
                return "모든 에이전트 실행에 실패했습니다."
            
            # 에이전트 타입별로 응답 분류
            response_by_type = {}
            for resp in successful_responses:
                agent_type = resp["agent_type"]
                if agent_type not in response_by_type:
                    response_by_type[agent_type] = []
                response_by_type[agent_type].append(resp["content"])
            
            # 통합된 응답 생성
            combined_response = "여러 에이전트의 결과를 통합했습니다:\n\n"
            
            for agent_type, responses in response_by_type.items():
                combined_response += f"**{agent_type.upper()} 에이전트 결과:**\n"
                for i, response in enumerate(responses, 1):
                    combined_response += f"{response}\n"
                combined_response += "\n"
            
            return combined_response
            
        except Exception as e:
            logger.error(f"에이전트 응답 통합 오류: {e}", exc_info=True)
            for resp in agent_responses:
                if resp["success"]:
                    return resp["content"]
            return "에이전트 응답 통합 중 오류가 발생했습니다."
    
    async def process_user_intent_streaming(
        self, 
        user_intent: UserIntent
    ):
        """
        사용자 의도를 처리하고 단계별로 스트리밍 응답을 생성합니다.
        
        **무조건 순차 실행**: 이전 에이전트가 완전히 끝나야 다음 에이전트가 시작됩니다.
        
        Yields:
            dict: 이벤트 타입과 데이터
                - {"type": "plan", "agents": [...], "sub_tasks": {...}, "execution_mode": "sequential"}
                - {"type": "start", "agent": "...", "order": N, "total": M, "task": "..."}
                - {"type": "result", "agent": "...", "content": "...", "success": True/False, "metadata": {...}}
                - {"type": "error", "agent": "...", "error": "..."}
                - {"type": "complete", "total_agents": N, "successful": N, "failed": N}
        """
        import time
        start_time = time.time()
        completed_agents = []
        failed_agents = []
        previous_results = []
        
        try:
            # 1. 의도 분석 시작 이벤트 (프런트에서 로딩 상태 업데이트용)
            logger.info("=" * 70)
            logger.info("[MAS-STREAM] 의도 분석 시작 | user_id=%s | message='%s'",
                user_intent.user_id, 
                user_intent.message[:80] + "..." if len(user_intent.message) > 80 else user_intent.message)
            
            yield {
                "type": "analyzing",
                "message": "의도를 분석하고 있어요..."
            }
            
            intent_analysis = await self._analyze_intent_with_llm(user_intent.message)
            
            selected_agents = intent_analysis.get("selected_agents", ["chatbot"])
            sub_tasks = intent_analysis.get("sub_tasks", {})
            
            # 무조건 순차 실행 강제
            execution_mode = "sequential"
            
            logger.info("[MAS-STREAM] 의도 분석 완료")
            logger.info("  ├─ 선택된 에이전트: %s", selected_agents)
            logger.info("  ├─ 실행 모드: %s (강제)", execution_mode.upper())
            logger.info("  └─ 서브태스크: %d개", len(sub_tasks))
            
            # 2. 의도 분석 완료 이벤트 (서론 텍스트 먼저 전송)
            intro_text = self._shorten_intro_text(intent_analysis.get("intro_text", ""))
            yield {
                "type": "analyzed",
                "intro_text": intro_text,
                "agents": selected_agents,
                "agent_count": len(selected_agents)
            }
            
            # 3. 실행 계획 yield
            yield {
                "type": "plan",
                "agents": selected_agents,
                "sub_tasks": sub_tasks,
                "execution_mode": execution_mode,
                "reasoning": intent_analysis.get("reasoning", ""),
                "confidence": intent_analysis.get("confidence", 0.8)
            }
            
            # 3. 각 에이전트 **순차** 실행 (이전 에이전트 완료 후 다음 에이전트 시작)
            total_agents = len(selected_agents)
            
            logger.info("-" * 70)
            logger.info("[MAS-STREAM] 에이전트 순차 실행 시작 | %d개 에이전트", total_agents)
            
            for i, agent_type in enumerate(selected_agents):
                order = i + 1
                
                # 서브태스크 가져오기
                task_info = sub_tasks.get(agent_type, {})
                task = task_info.get("task", user_intent.message)
                focus = task_info.get("focus", "")
                
                # 실행 시작 이벤트
                logger.info("[MAS-STREAM]   ├─ [%d/%d] %s 시작 | task='%s'", 
                    order, total_agents, agent_type, task[:50])
                
                yield {
                    "type": "start",
                    "agent": agent_type,
                    "order": order,
                    "total": total_agents,
                    "task": task,
                    "focus": focus
                }
                
                # 에이전트 실행 (동기적으로 완료될 때까지 대기)
                try:
                    agent = agent_registry.get_agent(agent_type)
                    
                    if not agent:
                        error_msg = f"에이전트를 찾을 수 없습니다: {agent_type}"
                        logger.warning("[MAS-STREAM]   │  └─ %s: %s", agent_type, error_msg)
                        failed_agents.append(agent_type)
                        yield {
                            "type": "error",
                            "agent": agent_type,
                            "order": order,
                            "error": error_msg
                        }
                        continue  # 다음 에이전트로 진행
                    
                    # 에이전트 상태 구성 (이전 결과 포함)
                    agent_state = {
                        "question": task,  # 서브태스크 사용
                        "original_query": user_intent.message,  # 원본 쿼리 참고용
                        "focus": focus,
                        "user_id": user_intent.user_id,
                        "session_id": user_intent.context.get("session_id"),
                        "filters": user_intent.context.get("filters", {}),
                        "time_hint": user_intent.context.get("time_hint"),
                        "context": user_intent.context,
                        "previous_results": previous_results  # 이전 에이전트 결과들
                    }
                    
                    agent_start = time.time()
                    
                    # **에이전트 실행 - 완전히 끝날 때까지 대기**
                    result_state = agent.process(agent_state)
                    
                    elapsed = time.time() - agent_start
                    
                    success = result_state.get("success", True)
                    content = result_state.get("answer", "")
                    metadata = result_state.get("metadata", {})
                    
                    # 결과 저장 (다음 에이전트에 전달)
                    agent_result = {
                        "agent": agent_type,
                        "task": task,
                        "content": content,
                        "success": success,
                        "metadata": metadata
                    }
                    previous_results.append(agent_result)
                    
                    if success:
                        completed_agents.append(agent_type)
                        logger.info("[MAS-STREAM]   │  └─ %s 완료 (%.2f초)", agent_type, elapsed)
                    else:
                        failed_agents.append(agent_type)
                        logger.warning("[MAS-STREAM]   │  └─ %s 실패 (%.2f초)", agent_type, elapsed)
                    
                    # 결과 이벤트 yield (이전 에이전트 완료 후에만 도달)
                    yield {
                        "type": "result",
                        "agent": agent_type,
                        "order": order,
                        "content": content,
                        "success": success,
                        "metadata": metadata,
                        "elapsed_time": elapsed
                    }
                    
                    # requires_confirmation이 있으면 다음 에이전트 실행 중단
                    # (사용자 확인 후 별도로 처리해야 함)
                    if metadata.get("requires_confirmation"):
                        remaining_agents = selected_agents[i+1:]
                        if remaining_agents:
                            logger.info("[MAS-STREAM] 확인 대기 중 | 남은 에이전트: %s", remaining_agents)
                            yield {
                                "type": "waiting_confirmation",
                                "agent": agent_type,
                                "remaining_agents": remaining_agents,
                                "metadata": metadata
                            }
                        # 여기서 스트리밍 종료 (남은 에이전트는 사용자 확인 후 별도 처리)
                        total_time = time.time() - start_time
                        yield {
                            "type": "complete",
                            "total_agents": order,  # 현재까지 실행된 에이전트 수
                            "successful": len(completed_agents),
                            "failed": len(failed_agents),
                            "completed_agents": completed_agents,
                            "failed_agents": failed_agents,
                            "total_time": total_time,
                            "waiting_confirmation": True,
                            "remaining_agents": remaining_agents
                        }
                        return  # 스트리밍 종료
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error("[MAS-STREAM]   │  └─ %s 오류: %s", agent_type, error_msg, exc_info=True)
                    failed_agents.append(agent_type)
                    
                    # 실패해도 다음 에이전트 계속 진행
                    previous_results.append({
                        "agent": agent_type,
                        "task": task,
                        "content": f"오류: {error_msg}",
                        "success": False,
                        "metadata": {"error": error_msg}
                    })
                    
                    yield {
                        "type": "error",
                        "agent": agent_type,
                        "order": order,
                        "error": error_msg
                    }
            
            # 4. 완료 이벤트
            total_time = time.time() - start_time
            
            logger.info("[MAS-STREAM] 전체 실행 완료 | %.2f초", total_time)
            logger.info("  ├─ 성공: %s", completed_agents if completed_agents else "없음")
            logger.info("  └─ 실패: %s", failed_agents if failed_agents else "없음")
            logger.info("=" * 70)
            
            yield {
                "type": "complete",
                "total_agents": total_agents,
                "successful": len(completed_agents),
                "failed": len(failed_agents),
                "completed_agents": completed_agents,
                "failed_agents": failed_agents,
                "total_time": total_time
            }
            
        except Exception as e:
            logger.error("[MAS-STREAM] 치명적 오류: %s", e, exc_info=True)
            yield {
                "type": "fatal_error",
                "error": str(e),
                "completed": completed_agents
            }

    async def process_user_intent(
        self, 
        user_intent: UserIntent, 
        stream: bool = False
    ) -> SupervisorResponse:
        """사용자 의도를 처리하고 적절한 에이전트를 선택합니다."""
        try:
            # 초기 상태 설정
            initial_state = {
                "messages": [],
                "user_input": user_intent.message,
                "user_id": user_intent.user_id,
                "user_context": user_intent.context,
                "selected_agents": [],
                "execution_mode": "sequential",
                "reasoning": "",
                "agent_responses": [],
                "final_response": "",
                "agent_success": False,
                "agent_type": "",
                "agent_metadata": {},
                "available_agents": [],
                "stream": stream
            }
            
            # 스트리밍 모드이고 chatbot이 선택될 경우 특별 처리
            if stream:
                # 먼저 의도 분석만 수행
                intent_result = await self._analyze_intent_with_llm(user_intent.message)
                selected_agents = intent_result.get("selected_agents", ["chatbot"])
                
                # chatbot이 첫 번째 에이전트인 경우 스트리밍 generator 반환
                if selected_agents and selected_agents[0] == "chatbot":
                    return self._create_streaming_response(user_intent, intent_result)
            
            # 그래프 실행 (비스트리밍)
            result = await self.graph.ainvoke(initial_state)
            
            logger.info(
                "Supervisor 그래프 실행 결과: agent_type='%s', execution_mode='%s'",
                result.get("agent_type"),
                result.get("execution_mode", "sequential")
            )
            
            # 주요 에이전트의 메타데이터 추출
            primary_agent_metadata = {}
            agent_responses_list = result.get("agent_responses", [])
            if agent_responses_list:
                primary_agent_metadata = agent_responses_list[0].get("metadata", {})
            
            combined_metadata = {**result.get("agent_metadata", {}), **primary_agent_metadata}
            
            return SupervisorResponse(
                success=result["agent_success"],
                selected_agent=result.get("selected_agents", [result["agent_type"]])[0] if result.get("selected_agents") else result["agent_type"],
                response=AgentResponse(
                    success=result["agent_success"],
                    content=result["final_response"],
                    agent_type=result["agent_type"],
                    metadata=combined_metadata
                ),
                reasoning=result["reasoning"],
                metadata={
                    "available_agents": result["available_agents"],
                    "user_context": user_intent.context,
                    "graph_execution": True,
                    "execution_mode": result.get("execution_mode", "sequential"),
                    "selected_agents": result.get("selected_agents", []),
                    "agent_responses": result.get("agent_responses", [])
                }
            )
            
        except Exception as e:
            logger.error(f"process_user_intent 오류: {e}", exc_info=True)
            return SupervisorResponse(
                success=False,
                selected_agent="unknown",
                response=AgentResponse(
                    success=False,
                    content=f"처리 중 오류가 발생했습니다: {str(e)}",
                    agent_type="unknown"
                ),
                reasoning=f"오류: {str(e)}",
                metadata={"error": str(e), "graph_execution": True}
            )
    
    def _create_streaming_response(
        self, 
        user_intent: UserIntent, 
        intent_result: Dict[str, Any]
    ) -> SupervisorResponse:
        """chatbot 스트리밍을 위한 응답을 생성합니다."""
        from agents.chatbot_agent.rag.react_agent import process as react_process
        from agents.chatbot_agent.rag.answerer import compose_answer
        
        def stream_generator():
            """스트리밍 generator"""
            try:
                state = {
                    "question": user_intent.message,
                    "user_id": user_intent.user_id,
                    "filters": {}
                }
                result = react_process(state)
                evidences = result.get("evidence", [])
                
                for chunk in compose_answer(user_intent.message, evidences, user_intent.user_id):
                    yield chunk
            except Exception as e:
                logger.error(f"스트리밍 응답 생성 중 오류: {e}", exc_info=True)
                yield f"오류가 발생했습니다: {str(e)}"
        
        return SupervisorResponse(
            success=True,
            selected_agent="chatbot",
            response=AgentResponse(
                success=True,
                content="",  # 스트리밍이므로 내용은 generator에서
                agent_type="chatbot",
                metadata={}
            ),
            reasoning=intent_result.get("reasoning", "chatbot 스트리밍 응답"),
            metadata={
                "streaming": True,
                "selected_agents": intent_result.get("selected_agents", ["chatbot"]),
                "execution_mode": intent_result.get("execution_mode", "sequential")
            },
            stream_generator=stream_generator
        )
    
    def get_available_agents(self) -> Dict[str, str]:
        """사용 가능한 에이전트 목록을 반환합니다."""
        return self.agent_descriptions
    
    def add_agent(self, agent_type: str, agent: BaseAgent):
        """새로운 에이전트를 추가합니다."""
        agent_registry.register_agent(agent)
        self.agent_descriptions = agent_registry.get_agent_descriptions()
    
    def remove_agent(self, agent_type: str):
        """에이전트를 제거합니다."""
        agent_registry.unregister_agent(agent_type)
        self.agent_descriptions = agent_registry.get_agent_descriptions()
    
    async def process_remaining_agents_streaming(
        self,
        user_intent: UserIntent,
        remaining_agents: List[str],
        sub_tasks: Dict[str, Any],
        previous_results: List[Dict[str, Any]] = None
    ):
        """
        남은 에이전트들을 순차적으로 실행합니다.
        
        이전 에이전트(예: report)가 확인을 받은 후 호출되어
        남은 에이전트들(예: coding)을 실행합니다.
        
        Args:
            user_intent: 사용자 의도
            remaining_agents: 실행할 에이전트 목록
            sub_tasks: 각 에이전트의 서브태스크 정보
            previous_results: 이전 에이전트들의 실행 결과
        
        Yields:
            dict: 이벤트 타입과 데이터
        """
        import time
        start_time = time.time()
        completed_agents = []
        failed_agents = []
        prev_results = previous_results or []
        
        try:
            total_agents = len(remaining_agents)
            
            logger.info("-" * 70)
            logger.info("[MAS-CONTINUE] 남은 에이전트 실행 시작 | %d개 에이전트", total_agents)
            logger.info("  └─ 에이전트 목록: %s", " → ".join(remaining_agents))
            
            # 실행 계획 yield (남은 에이전트만)
            yield {
                "type": "plan",
                "agents": remaining_agents,
                "sub_tasks": sub_tasks,
                "execution_mode": "sequential",
                "is_continuation": True
            }
            
            for i, agent_type in enumerate(remaining_agents):
                order = i + 1
                
                # 서브태스크 가져오기
                task_info = sub_tasks.get(agent_type, {})
                task = task_info.get("task", user_intent.message)
                focus = task_info.get("focus", "")
                
                # 실행 시작 이벤트
                logger.info("[MAS-CONTINUE]   ├─ [%d/%d] %s 시작 | task='%s'", 
                    order, total_agents, agent_type, task[:50])
                
                yield {
                    "type": "start",
                    "agent": agent_type,
                    "order": order,
                    "total": total_agents,
                    "task": task,
                    "focus": focus
                }
                
                # 에이전트 실행
                try:
                    agent = agent_registry.get_agent(agent_type)
                    
                    if not agent:
                        error_msg = f"에이전트를 찾을 수 없습니다: {agent_type}"
                        logger.warning("[MAS-CONTINUE]   │  └─ %s: %s", agent_type, error_msg)
                        failed_agents.append(agent_type)
                        yield {
                            "type": "error",
                            "agent": agent_type,
                            "order": order,
                            "error": error_msg
                        }
                        continue
                    
                    # 에이전트 상태 구성 (이전 결과 포함)
                    agent_state = {
                        "question": task,
                        "original_query": user_intent.message,
                        "focus": focus,
                        "user_id": user_intent.user_id,
                        "session_id": user_intent.context.get("session_id"),
                        "filters": user_intent.context.get("filters", {}),
                        "time_hint": user_intent.context.get("time_hint"),
                        "context": user_intent.context,
                        "previous_results": prev_results
                    }
                    
                    agent_start = time.time()
                    result_state = agent.process(agent_state)
                    elapsed = time.time() - agent_start
                    
                    success = result_state.get("success", True)
                    content = result_state.get("answer", "")
                    metadata = result_state.get("metadata", {})
                    
                    # 결과 저장
                    agent_result = {
                        "agent": agent_type,
                        "task": task,
                        "content": content,
                        "success": success,
                        "metadata": metadata
                    }
                    prev_results.append(agent_result)
                    
                    if success:
                        completed_agents.append(agent_type)
                        logger.info("[MAS-CONTINUE]   │  └─ %s 완료 (%.2f초)", agent_type, elapsed)
                    else:
                        failed_agents.append(agent_type)
                        logger.warning("[MAS-CONTINUE]   │  └─ %s 실패 (%.2f초)", agent_type, elapsed)
                    
                    # 결과 이벤트 yield
                    yield {
                        "type": "result",
                        "agent": agent_type,
                        "order": order,
                        "content": content,
                        "success": success,
                        "metadata": metadata,
                        "elapsed_time": elapsed
                    }
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error("[MAS-CONTINUE]   │  └─ %s 오류: %s", agent_type, error_msg, exc_info=True)
                    failed_agents.append(agent_type)
                    
                    prev_results.append({
                        "agent": agent_type,
                        "task": task,
                        "content": f"오류: {error_msg}",
                        "success": False,
                        "metadata": {"error": error_msg}
                    })
                    
                    yield {
                        "type": "error",
                        "agent": agent_type,
                        "order": order,
                        "error": error_msg
                    }
            
            # 완료 이벤트
            total_time = time.time() - start_time
            
            logger.info("[MAS-CONTINUE] 남은 에이전트 실행 완료 | %.2f초", total_time)
            logger.info("  ├─ 성공: %s", completed_agents if completed_agents else "없음")
            logger.info("  └─ 실패: %s", failed_agents if failed_agents else "없음")
            logger.info("=" * 70)
            
            yield {
                "type": "complete",
                "total_agents": total_agents,
                "successful": len(completed_agents),
                "failed": len(failed_agents),
                "completed_agents": completed_agents,
                "failed_agents": failed_agents,
                "total_time": total_time,
                "is_continuation": True
            }
            
        except Exception as e:
            logger.error("[MAS-CONTINUE] 치명적 오류: %s", e, exc_info=True)
            yield {
                "type": "fatal_error",
                "error": str(e),
                "completed": completed_agents
            }


# 전역 Supervisor 인스턴스 (LangGraph 기반)
_supervisor_instance: Optional[LangGraphSupervisor] = None
_supervisor_lock = threading.Lock()


def get_supervisor() -> LangGraphSupervisor:
    """Lazy singleton accessor for LangGraphSupervisor."""
    global _supervisor_instance
    if _supervisor_instance is None:
        with _supervisor_lock:
            if _supervisor_instance is None:
                _supervisor_instance = LangGraphSupervisor()
    return _supervisor_instance
