"""Embedding models for the document repository bounded context."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EmbeddingType(str, Enum):
    """Types of embedding vectors."""

    DOCUMENT = "document"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    QUERY = "query"
    CHUNK = "chunk"
    TEXT = "text"  # Generic text embedding


class StoredEmbedding(BaseModel):
    """Represents a stored embedding vector."""

    id: UUID | int
    entity_id: UUID | int | str
    vector: list[float]
    embedding_type: EmbeddingType = EmbeddingType.DOCUMENT
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    model_name: str | None = None
    dimensions: int | None = None
