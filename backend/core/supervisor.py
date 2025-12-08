import os
import sys
from pathlib import Path

# 현재 스크립트의 상위 디렉토리(backend)를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from typing import Dict, Any, Optional, List, Annotated, Sequence, TypedDict, TYPE_CHECKING
from pydantic import BaseModel
from langchain_community.chat_models import ChatOpenAI
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

class AgentState(TypedDict):
    """LangGraph 상태 정의"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_input: str
    user_id: Optional[int]
    user_context: Dict[str, Any]
    selected_agents: List[str]  # 여러 에이전트 지원
    reasoning: str
    agent_responses: List[Dict[str, Any]]  # 여러 에이전트 응답
    final_response: str  # 통합된 최종 응답
    agent_success: bool
    agent_type: str  # 주요 에이전트 타입
    agent_metadata: Dict[str, Any]
    available_agents: List[str]

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
            
            # Gemini API 우선 사용
            if settings.GEMINI_API_KEY:
                logger.info("Gemini API 초기화 시도...")
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel(settings.GEMINI_MODEL)
                logger.info("Gemini API 초기화 성공")
                return model
            else:
                logger.warning("GEMINI_API_KEY가 설정되지 않았습니다.")
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
        """사용자 의도를 분석하는 노드"""
        try:
            user_input = state["user_input"]
            
            # LLM을 사용한 의도 분석 (여러 에이전트 지원)
            intent_analysis = await self._analyze_intent_with_llm(user_input)
            
            new_state = state.copy()
            new_state["reasoning"] = intent_analysis["reasoning"]
            new_state["selected_agents"] = intent_analysis["selected_agents"]
            new_state["intent_analysis"] = intent_analysis
            
            return new_state
        except Exception as e:
            logger.error(f"의도 분석 오류: {e}", exc_info=True)
            # 오류 발생 시 기본값 설정
            new_state = state.copy()
            new_state["reasoning"] = "의도 분석 중 오류가 발생했습니다."
            new_state["selected_agents"] = ["chatbot"]
            return new_state

    async def _analyze_intent_with_llm(self, user_input: str) -> Dict[str, Any]:
        """LLM을 사용하여 사용자 의도를 분석합니다 (여러 에이전트 지원)."""
        try:
            # LLM이 초기화되지 않은 경우 기본 응답 반환
            if self.llm is None:
                logger.warning("LLM이 초기화되지 않았습니다. 기본 의도 분석을 사용합니다.")
                return self._fallback_intent_analysis(user_input)
            
            # LLM 프롬프트 생성
            prompt = self._create_llm_intent_prompt(user_input)
            
            if hasattr(self.llm, 'generate_content'):
                # Gemini API 사용
                response = self.llm.generate_content(prompt)
                analysis_text = response.text
            else:
                # LangChain 모델 사용
                from langchain_core.messages import HumanMessage
                messages = [HumanMessage(content=prompt)]
                response = await self.llm.ainvoke(messages)
                analysis_text = response.content
            
            # LLM 응답 파싱
            parsed_analysis = self._parse_llm_response(analysis_text)
            
            return parsed_analysis
            
        except Exception as e:
            logger.error(f"LLM 의도 분석 오류: {e}", exc_info=True)
            return self._fallback_intent_analysis(user_input)

    def _fallback_intent_analysis(self, user_input: str) -> Dict[str, Any]:
        """LLM이 없을 때의 기본 의도 분석"""
        user_input_lower = user_input.lower()
        
        # 키워드 기반 의도 분석
        if any(keyword in user_input_lower for keyword in ['코드', '프로그래밍', '개발', '함수', '클래스', '변수']):
            return {
                "intent": "coding",
                "confidence": 0.8,
                "reasoning": "코딩 관련 키워드가 감지되었습니다.",
                "selected_agents": ["coding_agent"]
            }
        elif any(keyword in user_input_lower for keyword in ['대시보드', '차트', '그래프', '분석', '트렌드', '통계', '패턴', '비교']):
            return {
                "intent": "dashboard",
                "confidence": 0.8,
                "reasoning": "대시보드/분석 관련 키워드가 감지되었습니다.",
                "selected_agents": ["dashboard"]
            }
        elif any(keyword in user_input_lower for keyword in ['추천', '추천해', '추천해줘', '어떤', '뭐가 좋을까']):
            return {
                "intent": "recommendation",
                "confidence": 0.8,
                "reasoning": "추천 관련 키워드가 감지되었습니다.",
                "selected_agents": ["recommendation_agent"]
            }
        elif any(keyword in user_input_lower for keyword in ['보고서', '리포트', 'report', '보고서 생성', '보고서 작성', '보고서를 만들어', '보고서를 작성해']):
            return {
                "intent": "report",
                "confidence": 0.85,
                "reasoning": "보고서 생성 관련 키워드가 감지되었습니다.",
                "selected_agents": ["report"]
            }
        else:
            return {
                "intent": "chat",
                "confidence": 0.6,
                "reasoning": "기본 채팅 의도로 분류되었습니다.",
                "selected_agents": ["chatbot"]
            }

    def _create_llm_intent_prompt(self, user_input: str) -> str:
        """LLM 의도 분석을 위한 프롬프트를 생성합니다."""
        agent_descriptions = "\n".join([
            f"- {agent_type}: {description}"
            for agent_type, description in self.agent_descriptions.items()
        ])
        
        return f"""
당신은 사용자의 의도를 분석하여 적절한 AI 에이전트를 선택하는 전문가입니다.

사용 가능한 에이전트들:
{agent_descriptions}

사용자의 메시지를 분석하여 어떤 에이전트가 가장 적합한지 판단하고, 그 이유를 설명해주세요.
복잡한 요청의 경우 여러 에이전트를 조합하여 사용할 수 있습니다.

분석 결과는 다음과 같은 JSON 형식으로 제공해주세요:
{{
    "selected_agents": ["에이전트1", "에이전트2"],
    "primary_agent": "주요 에이전트명",
    "confidence": 0.95,
    "reasoning": "선택 이유와 에이전트 조합 이유 설명",
    "keywords": ["키워드1", "키워드2"],
    "intent": "사용자 의도 요약",
    "agent_workflow": "에이전트 실행 순서와 역할 설명"
}}

에이전트 선택 기준:
- coding: 코드 작성, 디버깅, 프로그래밍 관련 질문
- dashboard: 사용자 데이터 분석, 관심사 트렌드 분석, 활동 패턴 분석, 기간 비교, 통계 분석 요청 (예: "내 활동 분석해줘", "관심사 트렌드 보여줘", "이번 주 저번 주 비교해줘")
- recommendation: 추천, 제안, 추천해줘 등의 요청
- report: 보고서 생성, 리포트 작성, 특정 주제에 대한 심층 보고서 요청 (외부 웹 검색 기반)
- chatbot: 일반적인 질문, 이미지 분석, 멀티모달 질문

복합 요청 예시:
- "코드를 작성하고 대시보드로 시각화해줘" → ["coding", "dashboard"]
- "추천해주고 분석 결과를 차트로 보여줘" → ["recommendation", "dashboard"]
- "이미지를 분석하고 코드로 구현해줘" → ["chatbot", "coding"]

대시보드 분석 요청 예시:
- "내 활동 분석해줘" → ["dashboard"]
- "관심사 트렌드 보여줘" → ["dashboard"]
- "이번 주 저번 주 비교해줘" → ["dashboard"]
- "추천 수락률 분석해줘" → ["dashboard"]

보고서 요청 예시:
- "Docker에 대한 보고서를 작성해줘" → ["report"]
- "그 주제에 대해 보고서를 만들어줘" → ["report"] (이전 대화 컨텍스트에서 주제 추출)
- "방금 얘기한 내용으로 리포트 생성해줘" → ["report"]

사용자 메시지: {user_input}

JSON 응답만 제공해주세요:
"""

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
                if "selected_agents" not in parsed:
                    parsed["selected_agents"] = ["chatbot"]
                if "primary_agent" not in parsed:
                    parsed["primary_agent"] = parsed["selected_agents"][0] if parsed["selected_agents"] else "chatbot"
                if "reasoning" not in parsed:
                    parsed["reasoning"] = "LLM 분석 결과"
                if "confidence" not in parsed:
                    parsed["confidence"] = 0.8
                if "keywords" not in parsed:
                    parsed["keywords"] = []
                if "intent" not in parsed:
                    parsed["intent"] = "사용자 의도 분석"
                if "agent_workflow" not in parsed:
                    parsed["agent_workflow"] = "에이전트 실행 순서: " + " → ".join(parsed["selected_agents"])
                
                return parsed
            else:
                # JSON이 없으면 키워드 기반 폴백
                return {}
                
        except Exception as e:
            logger.error(f"LLM 응답 파싱 오류: {e}", exc_info=True)
            return {}

    async def _agent_selector_node(self, state: AgentState) -> AgentState:
        """적절한 에이전트를 선택하는 노드"""
        try:
            user_input = state["user_input"]
            reasoning = state["reasoning"]
            selected_agents = state.get("selected_agents", ["chatbot"])
            
            new_state = state.copy()
            new_state["selected_agents"] = selected_agents
            new_state["available_agents"] = list(self.agent_descriptions.keys())
            return new_state
            
        except Exception as e:
            new_state = state.copy()
            new_state["selected_agents"] = ["chatbot"]  # 기본값
            new_state["reasoning"] += f" 에이전트 선택 중 오류: {str(e)}"
            return new_state
    
    async def _agent_executor_node(self, state: AgentState) -> AgentState:
        """선택된 여러 에이전트를 순차적으로 실행하는 노드"""
        try:
            selected_agents = state["selected_agents"]
            user_input = state["user_input"]
            user_id = state["user_id"]
            
            agent_responses = []
            final_response = ""
            agent_success = True
            primary_agent_type = "unknown"
            
            # 각 에이전트를 순차적으로 실행
            for i, agent_type in enumerate(selected_agents):
                try:
                    # 에이전트 인스턴스 가져오기
                    agent = agent_registry.get_agent(agent_type)
                    
                    if agent:
                        # 새로운 process(state) 패턴에 맞는 상태 생성
                        agent_state = {
                            "question": user_input,
                            "user_id": user_id,
                            "session_id": state.get("user_context", {}).get("session_id"),
                            "filters": state.get("user_context", {}).get("filters", {}),
                            "time_hint": state.get("user_context", {}).get("time_hint"),
                            "context": state.get("user_context", {})
                        }
                        
                        # 새로운 process(state) 패턴으로 에이전트 실행
                        if hasattr(agent, 'process') and callable(getattr(agent, 'process')):
                            # 새로운 패턴 사용
                            result_state = agent.process(agent_state)
                            
                            # 응답 수집
                            agent_response = {
                                "agent_type": agent_type,
                                "content": result_state.get("answer", ""),
                                "success": True,  # process()는 성공 시에만 호출됨
                                "metadata": result_state.get("metadata", {}),
                                "evidence": result_state.get("evidence", []),
                                "order": i + 1
                            }
                        else:
                            # 기존 async 패턴 사용 (호환성)
                            result = await agent.process_async(user_input, user_id)
                            agent_response = {
                                "agent_type": agent_type,
                                "content": result.content,
                                "success": result.success,
                                "metadata": result.metadata,
                                "evidence": [],
                                "order": i + 1
                            }
                        
                        agent_responses.append(agent_response)
                        
                        # 첫 번째 에이전트를 주요 에이전트로 설정
                        if i == 0:
                            primary_agent_type = agent_type
                            final_response = agent_response["content"]
                        
                        # 에이전트 실행 실패 시 전체 실패로 처리
                        if not agent_response["success"]:
                            agent_success = False
                            
                    else:
                        # 에이전트를 찾을 수 없는 경우
                        agent_responses.append({
                            "agent_type": agent_type,
                            "content": f"에이전트를 찾을 수 없습니다: {agent_type}",
                            "success": False,
                            "metadata": {},
                            "evidence": [],
                            "order": i + 1
                        })
                        agent_success = False
                        
                except Exception as e:
                    # 개별 에이전트 실행 오류
                    agent_responses.append({
                        "agent_type": agent_type,
                        "content": f"에이전트 실행 중 오류: {str(e)}",
                        "success": False,
                        "metadata": {"error": str(e)},
                        "evidence": [],
                        "order": i + 1
                    })
                    agent_success = False
            
            # 여러 에이전트 응답을 통합
            if len(agent_responses) > 1:
                final_response = self._combine_agent_responses(agent_responses, user_input)
            
            new_state = state.copy()
            new_state["agent_responses"] = agent_responses
            new_state["final_response"] = final_response
            new_state["agent_success"] = agent_success
            new_state["agent_type"] = primary_agent_type
            new_state["agent_metadata"] = {
                "total_agents": len(selected_agents),
                "executed_agents": [resp["agent_type"] for resp in agent_responses],
                "successful_agents": [resp["agent_type"] for resp in agent_responses if resp["success"]],
                "failed_agents": [resp["agent_type"] for resp in agent_responses if not resp["success"]]
            }
            
            return new_state
                
        except Exception as e:
            new_state = state.copy()
            new_state["agent_responses"] = []
            new_state["final_response"] = f"에이전트 실행 중 오류: {str(e)}"
            new_state["agent_success"] = False
            new_state["agent_type"] = "unknown"
            new_state["agent_metadata"] = {"error": str(e)}
            return new_state
    
    def _create_intent_analysis_prompt(self, user_message: str) -> str:
        """의도 분석을 위한 프롬프트를 생성합니다."""
        agent_descriptions = "\n".join([
            f"- {agent_type}: {description}"
            for agent_type, description in self.agent_descriptions.items()
        ])
        
        return f"""
당신은 사용자의 의도를 분석하여 적절한 AI 에이전트를 선택하는 전문가입니다.

사용 가능한 에이전트들:
{agent_descriptions}

사용자의 메시지를 분석하여 어떤 에이전트가 가장 적합한지 판단하고, 그 이유를 설명해주세요.

분석 결과는 다음과 같은 형식으로 제공해주세요:
- 주요 키워드: [사용자 메시지에서 추출한 주요 키워드들]
- 의도 분석: [사용자가 원하는 것이 무엇인지 분석]
- 추천 에이전트: [가장 적합한 에이전트명]
- 선택 이유: [왜 해당 에이전트를 선택했는지 설명]

사용자 메시지: {user_message}
"""
    
    async def process_user_intent(self, user_intent: UserIntent) -> SupervisorResponse:
        """사용자 의도를 처리하고 적절한 에이전트를 선택합니다 (LangGraph 기반)."""
        try:
            # 초기 상태 설정
            initial_state = {
                "messages": [],
                "user_input": user_intent.message,
                "user_id": user_intent.user_id,
                "user_context": user_intent.context,
                "selected_agents": [],
                "reasoning": "",
                "agent_responses": [],
                "final_response": "",
                "agent_success": False,
                "agent_type": "",
                "agent_metadata": {},
                "available_agents": []
            }
            
            # 그래프 실행
            result = await self.graph.ainvoke(initial_state)
            logger.info("Supervisor 그래프 실행 결과: final_response_preview='%s', agent_responses=%s",
                         result.get("final_response"),
                         [
                             {
                                 "agent_type": resp.get("agent_type"),
                                 "success": resp.get("success"),
                                 "content_preview": (resp.get("content") or "")[:120]
                             }
                             for resp in result.get("agent_responses", [])
                         ])
            
            # 주요 에이전트(첫 번째)의 메타데이터 추출
            primary_agent_metadata = {}
            agent_responses_list = result.get("agent_responses", [])
            if agent_responses_list:
                primary_agent_metadata = agent_responses_list[0].get("metadata", {})
            
            # agent_metadata와 primary_agent_metadata 병합
            combined_metadata = {**result["agent_metadata"], **primary_agent_metadata}
            
            # 결과를 SupervisorResponse로 변환
            return SupervisorResponse(
                success=result["agent_success"],
                selected_agent=result.get("selected_agents", [result["agent_type"]])[0],  # 첫 번째 에이전트
                response=AgentResponse(
                    success=result["agent_success"],
                    content=result["final_response"],
                    agent_type=result["agent_type"],
                    metadata=combined_metadata  # 에이전트의 메타데이터 포함
                ),
                reasoning=result["reasoning"],
                metadata={
                    "available_agents": result["available_agents"],
                    "user_context": user_intent.context,
                    "graph_execution": True,
                    "selected_agents": result.get("selected_agents", []),
                    "agent_responses": result.get("agent_responses", [])
                }
            )
            
        except Exception as e:
            return SupervisorResponse(
                success=False,
                selected_agent="unknown",
                response=AgentResponse(
                    success=False,
                    content=f"LangGraph 처리 중 오류가 발생했습니다: {str(e)}",
                    agent_type="unknown"
                ),
                reasoning="그래프 실행 중 오류가 발생했습니다.",
                metadata={"error": str(e), "graph_execution": True}
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

    def _combine_agent_responses(self, agent_responses: List[Dict[str, Any]], user_input: str) -> str:
        """여러 에이전트의 응답을 지능적으로 통합합니다."""
        try:
            if not agent_responses:
                return "에이전트 응답이 없습니다."
            
            if len(agent_responses) == 1:
                return agent_responses[0]["content"]
            
            # 성공한 에이전트 응답만 필터링
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
                    combined_response += f"{i}. {response}\n"
                combined_response += "\n"
            
            # 요약 추가
            combined_response += f"총 {len(successful_responses)}개의 에이전트가 성공적으로 실행되었습니다."
            
            return combined_response
            
        except Exception as e:
            logger.error(f"에이전트 응답 통합 오류: {e}", exc_info=True)
            # 오류 발생 시 첫 번째 성공한 응답 반환
            for resp in agent_responses:
                if resp["success"]:
                    return resp["content"]
            return "에이전트 응답 통합 중 오류가 발생했습니다."

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