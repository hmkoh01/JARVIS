from typing import Dict, List, Type, Annotated, Sequence, TYPE_CHECKING
from agents.chatbot_agent.chatbot_agent import ChatbotAgent
from agents.coding_agent.coding_agent import CodingAgent
from agents.dashboard_agent.dashboard_agent import DashboardAgent
from agents.recommendation_agent.recommendation_agent import RecommendationAgent
from agents.report_agent.report_agent import ReportAgent
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

if TYPE_CHECKING:
    from agents.base_agent import BaseAgent
else:
    BaseAgent = None

class AgentRegistry:
    """에이전트들을 등록하고 관리하는 레지스트리 (LangGraph 호환)"""
    
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}
        self._agent_nodes: Dict[str, callable] = {}
        self._register_default_agents()
    
    def _register_default_agents(self):
        """기본 에이전트들을 등록합니다."""
        default_agents = [
            ChatbotAgent(),  # 멀티모달 RAG 챗봇 에이전트
            CodingAgent(),
            DashboardAgent(),
            RecommendationAgent(),
            ReportAgent(),  # 웹 검색 기반 보고서 생성 에이전트
        ]
        
        for agent in default_agents:
            self.register_agent(agent)
    
    def register_agent(self, agent: BaseAgent):
        """새로운 에이전트를 등록합니다."""
        self._agents[agent.agent_type] = agent
        
        # LangGraph 노드 함수 생성 (새로운 process(state) 패턴 지원)
        async def agent_node(state):
            """에이전트 실행 노드"""
            try:
                user_input = state.get("user_input", "")
                user_id = state.get("user_id")
                
                # 새로운 process(state) 패턴 지원
                if hasattr(agent, 'process') and callable(getattr(agent, 'process')):
                    # 새로운 패턴 사용
                    agent_state = {
                        "question": user_input,
                        "user_id": user_id,
                        "session_id": state.get("user_context", {}).get("session_id"),
                        "filters": state.get("user_context", {}).get("filters", {}),
                        "time_hint": state.get("user_context", {}).get("time_hint"),
                        "context": state.get("user_context", {})
                    }
                    
                    result_state = agent.process(agent_state)
                    
                    # 상태 업데이트
                    new_state = state.copy()
                    new_state["agent_response"] = result_state.get("answer", "")
                    new_state["agent_success"] = result_state.get("success", True)
                    new_state["agent_type"] = agent.agent_type
                    new_state["agent_metadata"] = result_state.get("metadata", {})
                    
                    return new_state
                else:
                    # 기존 async 패턴 사용 (호환성)
                    response = await agent.process_async(user_input, user_id)
                    
                    # 상태 업데이트
                    new_state = state.copy()
                    new_state["agent_response"] = response.content
                    new_state["agent_success"] = response.success
                    new_state["agent_type"] = agent.agent_type
                    new_state["agent_metadata"] = response.metadata or {}
                    
                    return new_state
                    
            except Exception as e:
                new_state = state.copy()
                new_state["agent_response"] = f"에이전트 실행 중 오류: {str(e)}"
                new_state["agent_success"] = False
                new_state["agent_type"] = agent.agent_type
                return new_state
        
        self._agent_nodes[agent.agent_type] = agent_node
    
    def unregister_agent(self, agent_type: str):
        """에이전트를 등록 해제합니다."""
        if agent_type in self._agents:
            del self._agents[agent_type]
        if agent_type in self._agent_nodes:
            del self._agent_nodes[agent_type]
    
    def get_agent(self, agent_type: str) -> BaseAgent:
        """에이전트를 가져옵니다."""
        return self._agents.get(agent_type)
    
    def get_agent_node(self, agent_type: str) -> callable:
        """에이전트 노드 함수를 가져옵니다."""
        return self._agent_nodes.get(agent_type)
    
    def get_all_agents(self) -> List[BaseAgent]:
        """모든 에이전트를 반환합니다."""
        return list(self._agents.values())
    
    def get_agent_types(self) -> List[str]:
        """모든 에이전트 타입을 반환합니다."""
        return list(self._agents.keys())
    
    def get_agent_descriptions(self) -> Dict[str, str]:
        """에이전트 타입별 설명을 반환합니다."""
        return {agent_type: agent.description for agent_type, agent in self._agents.items()}
    
    def get_all_agent_nodes(self) -> Dict[str, callable]:
        """모든 에이전트 노드 함수를 반환합니다."""
        return self._agent_nodes.copy()

# 전역 에이전트 레지스트리 인스턴스
agent_registry = AgentRegistry() 