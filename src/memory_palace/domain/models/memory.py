"""Memory chunk domain model."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from memory_palace.domain.models.utils import utc_now


class MemoryChunk(BaseModel):
    """Canonical schema for every remembered utterance."""

    id: UUID = Field(default_factory=uuid4)
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=utc_now)
    embedding: list[float] | None = None
    topic_id: int | None = None
    ontology_path: list[str] = Field(default_factory=list)
    salience: float = Field(default=1.0, ge=0.0, le=1.0)
