from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime

class UserIntent(BaseModel):
    """사용자 의도 모델"""
    user_id: Optional[int] = None
    message: str
    context: Dict[str, Any] = {}

class ChatRequest(BaseModel):
    """채팅 요청 모델 (deprecated: MessageRequest 사용 권장)"""
    message: str
    user_id: Optional[int] = None

class ChatResponse(BaseModel):
    """채팅 응답 모델 (deprecated: MessageResponse 사용 권장)"""
    success: bool
    message: str
    agent_type: str
    metadata: Dict[str, Any] = {}


class MessageRequest(BaseModel):
    """통합 메시지 요청 모델"""
    message: str
    user_id: Optional[int] = None
    stream: bool = True  # 기본값 스트리밍


class MessageResponse(BaseModel):
    """통합 메시지 응답 모델"""
    success: bool
    content: str
    agent_type: str
    metadata: Dict[str, Any] = {}

class SupervisorResponse(BaseModel):
    """Supervisor 응답 모델"""
    success: bool
    content: str
    agent_type: str
    metadata: Dict[str, Any] = {}
    timestamp: Optional[datetime] = None

class AgentResponse(BaseModel):
    """에이전트 응답 모델"""
    success: bool
    content: str
    agent_type: str
    metadata: Dict[str, Any] = {}

class DataCollectionStatus(BaseModel):
    """데이터 수집 상태 모델"""
    active_users: List[int]
    total_managers: int
    managers_info: Dict[str, Any]
    timestamp: datetime

class DataCollectionStats(BaseModel):
    """데이터 수집 통계 모델"""
    total_records: Dict[str, int]
    last_24_hours: Dict[str, int]
    active_collectors: int
    timestamp: datetime

class FileInfo(BaseModel):
    """파일 정보 모델"""
    file_path: str
    file_name: str
    file_size: int
    file_type: str
    file_category: str
    created_date: Optional[datetime] = None
    modified_date: Optional[datetime] = None
    accessed_date: Optional[datetime] = None

class BrowserHistoryInfo(BaseModel):
    """브라우저 히스토리 정보 모델"""
    browser_name: str
    browser_version: str
    url: str
    title: str
    visit_count: int
    visit_time: datetime
    last_visit_time: datetime
    page_transition: str
    visit_duration: Optional[int] = None

class ActiveAppInfo(BaseModel):
    """활성 애플리케이션 정보 모델"""
    app_name: str
    app_path: str
    app_version: str
    app_category: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration: int
    window_title: Optional[str] = None
    window_state: Optional[str] = None
    cpu_usage: float
    memory_usage: float
