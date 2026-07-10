"""Embedding-related enums shared across the embedding services."""

from enum import StrEnum


class EmbeddingType(StrEnum):
    """Types of embedding vectors."""

    DOCUMENT = "document"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    QUERY = "query"
    CHUNK = "chunk"
    TEXT = "text"  # Generic text embedding
