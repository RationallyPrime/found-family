"""Conversation models adapted from Automining for Memory Palace."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from memory_palace.domain.models.utils import utc_now


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
    timestamp: datetime = Field(default_factory=utc_now)
    content_type: ContentType = ContentType.TEXT
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Ontology support
    ontology_path: list[str] = Field(default_factory=list)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    topic_id: int | None = None

    def get_text_content(self) -> str:
        """Extract text content from the message."""
        if self.content_type == ContentType.THINKING:
            return f"<thinking>\n{self.content}\n</thinking>"
        return self.content


class Conversation(BaseModel):
    """A complete conversation with analysis."""

    id: UUID = Field(default_factory=uuid4)
    title: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None
    messages: list[Message] = Field(default_factory=list)
    summary: str | None = None
    key_topics: list[str] = Field(default_factory=list)
    dominant_emotion: str | None = None
    quality_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_message_count(self) -> int:
        """Get total message count."""
        return len(self.messages)

    def get_duration(self) -> float | None:
        """Get conversation duration in seconds."""
        if len(self.messages) < 2:
            return None
        first = self.messages[0].timestamp
        last = self.messages[-1].timestamp
        return (last - first).total_seconds()

    def to_transcript(self) -> str:
        """Convert to readable transcript."""
        lines = []
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")

        for message in self.messages:
            lines.append(f"### {message.role.value.capitalize()}")
            lines.append(message.get_text_content())
            lines.append("")

        return "\n".join(lines)
