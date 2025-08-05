"""Conversation models adapted from Automining for Memory Palace."""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Message roles in a conversation."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ContentType(str, Enum):
    """Content types in messages."""
    TEXT = "text"
    THINKING = "thinking"
    CODE = "code"
    IMAGE = "image"


class Message(BaseModel):
    """A single message in a conversation."""
    id: UUID = Field(default_factory=uuid4)
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    content_type: ContentType = ContentType.TEXT
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    def get_text_content(self) -> str:
        """Extract text content from the message."""
        if self.content_type == ContentType.THINKING:
            return f"<thinking>\n{self.content}\n</thinking>"
        return self.content


class ConversationTurn(BaseModel):
    """A turn in a conversation (user message + assistant response)."""
    id: UUID = Field(default_factory=uuid4)
    user_message: Message
    assistant_message: Message
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    topic_id: int | None = None
    ontology_path: list[str] = Field(default_factory=list)
    salience: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    """A complete conversation with analysis."""
    id: UUID = Field(default_factory=uuid4)
    title: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    summary: str | None = None
    key_topics: list[str] = Field(default_factory=list)
    dominant_emotion: str | None = None
    quality_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    def get_message_count(self) -> int:
        """Get total message count."""
        return len(self.turns) * 2  # Each turn has 2 messages
    
    def get_duration(self) -> float | None:
        """Get conversation duration in seconds."""
        if len(self.turns) < 2:
            return None
        first = self.turns[0].timestamp
        last = self.turns[-1].timestamp
        return (last - first).total_seconds()
    
    def to_transcript(self) -> str:
        """Convert to readable transcript."""
        lines = []
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")
            
        for turn in self.turns:
            lines.append(f"### User")
            lines.append(turn.user_message.get_text_content())
            lines.append("")
            lines.append(f"### Assistant")
            lines.append(turn.assistant_message.get_text_content())
            lines.append("")
            
        return "\n".join(lines)