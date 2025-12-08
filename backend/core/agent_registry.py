from typing import Dict, List, TYPE_CHECKING
from agents.chatbot_agent.chatbot_agent import ChatbotAgent
from agents.coding_agent.coding_agent import CodingAgent
from agents.dashboard_agent.dashboard_agent import DashboardAgent
from agents.recommendation_agent.recommendation_agent import RecommendationAgent
from agents.report_agent.report_agent import ReportAgent

if TYPE_CHECKING:
    from agents.base_agent import BaseAgent
else:
    BaseAgent = None


class AgentRegistry:
    """
    에이전트들을 등록하고 관리하는 레지스트리
    
    모든 에이전트는 process(state: Dict) -> Dict 패턴을 사용합니다.
    """
    
    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}
        self._register_default_agents()
    
    def _register_default_agents(self):
        """기본 에이전트들을 등록합니다."""
        default_agents = [
            ChatbotAgent(),
            CodingAgent(),
            DashboardAgent(),
            RecommendationAgent(),
            ReportAgent(),
        ]
        
        for agent in default_agents:
            self.register_agent(agent)
    
    def register_agent(self, agent: BaseAgent):
        """새로운 에이전트를 등록합니다."""
        self._agents[agent.agent_type] = agent
    
    def unregister_agent(self, agent_type: str):
        """에이전트를 등록 해제합니다."""
        if agent_type in self._agents:
            del self._agents[agent_type]
    
    def get_agent(self, agent_type: str) -> BaseAgent:
        """에이전트를 가져옵니다."""
        return self._agents.get(agent_type)
    
    def get_all_agents(self) -> List[BaseAgent]:
        """모든 에이전트를 반환합니다."""
        return list(self._agents.values())
    
    def get_agent_types(self) -> List[str]:
        """모든 에이전트 타입을 반환합니다."""
        return list(self._agents.keys())
    
    def get_agent_descriptions(self) -> Dict[str, str]:
        """에이전트 타입별 설명을 반환합니다."""
        return {agent_type: agent.description for agent_type, agent in self._agents.items()}


# 전역 에이전트 레지스트리 인스턴스
agent_registry = AgentRegistry()
