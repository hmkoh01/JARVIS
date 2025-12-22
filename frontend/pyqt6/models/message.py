"""
JARVIS Message Model
Data model for chat messages.

Phase 3: Updated with streaming support and metadata
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import uuid


class MessageRole(Enum):
    """Message role types."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """
    Represents a chat message.
    
    Attributes:
        id: Unique message identifier
        role: Message role (user/assistant/system)
        content: Message text content
        timestamp: When the message was created
        is_streaming: Whether message is still receiving content
        metadata: Optional additional data (citations, agent info, etc.)
    """
    
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_streaming: bool = False
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Validate role after initialization."""
        valid_roles = {r.value for r in MessageRole}
        if self.role not in valid_roles:
            raise ValueError(f"Invalid role: {self.role}. Must be one of {valid_roles}")
    
    @classmethod
    def user_message(cls, content: str, **kwargs) -> "Message":
        """Create a user message."""
        return cls(
            role=MessageRole.USER.value,
            content=content,
            **kwargs
        )
    
    @classmethod
    def assistant_message(cls, content: str, is_streaming: bool = False, **kwargs) -> "Message":
        """Create an assistant message."""
        return cls(
            role=MessageRole.ASSISTANT.value,
            content=content,
            is_streaming=is_streaming,
            **kwargs
        )
    
    @classmethod
    def system_message(cls, content: str, **kwargs) -> "Message":
        """Create a system message."""
        return cls(
            role=MessageRole.SYSTEM.value,
            content=content,
            **kwargs
        )
    
    @classmethod
    def streaming_message(cls) -> "Message":
        """Create an empty streaming message placeholder."""
        return cls(
            role=MessageRole.ASSISTANT.value,
            content="",
            is_streaming=True
        )
    
    def append_content(self, chunk: str) -> None:
        """Append content to the message (for streaming)."""
        self.content += chunk
    
    def complete_streaming(self) -> None:
        """Mark streaming as complete."""
        self.is_streaming = False
    
    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value."""
        if self.metadata is None:
            self.metadata = {}
        self.metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value."""
        if self.metadata is None:
            return default
        return self.metadata.get(key, default)
    
    @property
    def is_user(self) -> bool:
        """Check if this is a user message."""
        return self.role == MessageRole.USER.value
    
    @property
    def is_assistant(self) -> bool:
        """Check if this is an assistant message."""
        return self.role == MessageRole.ASSISTANT.value
    
    @property
    def is_system(self) -> bool:
        """Check if this is a system message."""
        return self.role == MessageRole.SYSTEM.value
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary."""
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "is_streaming": self.is_streaming,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create message from dictionary."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now()
        
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
            is_streaming=data.get("is_streaming", False),
            metadata=data.get("metadata")
        )
