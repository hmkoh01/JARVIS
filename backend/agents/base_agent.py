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
    """
    모든 에이전트의 기본 클래스
    
    모든 에이전트는 process(state: Dict) -> Dict 패턴을 구현해야 합니다.
    
    state 입력 형식:
        {
            "question": str,           # 사용자 질문/요청
            "user_id": Optional[int],  # 사용자 ID
            "session_id": Optional[str],
            "filters": Dict[str, Any],
            "context": Dict[str, Any]
        }
    
    반환 형식:
        {
            "answer": str,             # 응답 내용
            "success": bool,           # 처리 성공 여부
            "agent_type": str,         # 에이전트 타입
            "metadata": Dict[str, Any],# 추가 메타데이터
            "evidence": List[Any]      # 근거 자료 (선택)
        }
    """
    
    def __init__(self, agent_type: str, description: str):
        self.agent_type = agent_type
        self.description = description
        self.state = AgentState()
    
    @abstractmethod
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        사용자 요청을 처리합니다.
        
        Args:
            state: 상태 딕셔너리
                - question: 사용자 질문/요청
                - user_id: 사용자 ID
                - session_id: 세션 ID (선택)
                - filters: 필터 조건 (선택)
                - context: 추가 컨텍스트 (선택)
        
        Returns:
            처리 결과 딕셔너리
                - answer: 응답 내용
                - success: 처리 성공 여부
                - agent_type: 에이전트 타입
                - metadata: 추가 메타데이터
                - evidence: 근거 자료 (선택)
        """
        pass
    
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
