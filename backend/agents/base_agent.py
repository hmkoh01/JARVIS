from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class AgentState(BaseModel):
    """에이전트 상태를 나타내는 모델"""
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    context: Dict[str, Any] = {}
    history: List[Dict[str, Any]] = []

class AgentResponse(BaseModel):
    """에이전트 응답을 나타내는 모델"""
    success: bool
    content: Any
    agent_type: str
    metadata: Dict[str, Any] = {}

class BaseAgent(ABC):
    """모든 에이전트의 기본 클래스"""
    
    def __init__(self, agent_type: str, description: str):
        self.agent_type = agent_type
        self.description = description
        self.state = AgentState()
    
    @abstractmethod
    async def process(self, user_input: str, user_id: Optional[int] = None) -> AgentResponse:
        """사용자 입력을 처리합니다."""
        pass
    
    def add_tool(self, tool):
        """에이전트에 도구를 추가합니다. (현재는 사용하지 않음)"""
        pass
    
    def remove_tool(self, tool_name: str):
        """에이전트에서 도구를 제거합니다. (현재는 사용하지 않음)"""
        pass
    
    async def execute_tool(self, tool_name: str, **kwargs):
        """도구를 실행합니다. (현재는 사용하지 않음)"""
        return {
            'success': False,
            'data': None,
            'error': f"도구 기능이 비활성화되어 있습니다: {tool_name}"
        }
    
    def update_state(self, **kwargs):
        """에이전트 상태를 업데이트합니다."""
        for key, value in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
            else:
                self.state.context[key] = value
    
    def get_state(self) -> AgentState:
        """현재 에이전트 상태를 반환합니다."""
        return self.state 