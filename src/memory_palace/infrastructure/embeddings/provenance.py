"""Embedding-vector provenance assignment at persistence boundaries."""

from typing import Protocol

from memory_palace.domain.protocols import EmbeddingService


class EmbeddingBearing(Protocol):
    """Mutable persistence model carrying a vector and its space identity."""

    embedding: list[float] | None
    embedding_model: str | None
    embedding_dimensions: int | None


def attach_embedding_provenance(
    memory: EmbeddingBearing,
    vector: list[float],
    service: EmbeddingService,
) -> None:
    """Assign one validated vector together with its model-space identity."""
    dimensions = service.get_model_dimensions()
    if len(vector) != dimensions:
        raise ValueError(f"Embedding dimension mismatch: expected {dimensions}, got {len(vector)}")
    memory.embedding = vector
    memory.embedding_model = service.model
    memory.embedding_dimensions = dimensions
