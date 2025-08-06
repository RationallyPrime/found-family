"""Service layer interfaces and implementations."""

from typing import Protocol, runtime_checkable

from memory_palace.domain.models.embedding import EmbeddingType


@runtime_checkable
class EmbeddingService(Protocol):
    """Protocol for embedding services."""
    
    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        ...
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...


@runtime_checkable  
class ClusteringService(Protocol):
    """Protocol for clustering services."""
    
    async def predict(
        self,
        embeddings: list[list[float]]
    ) -> list[int]:
        """Predict cluster IDs for embeddings."""
        ...
    
    async def fit(
        self,
        embeddings: list[list[float]]
    ) -> None:
        """Fit the clustering model."""
        ...


__all__ = ["EmbeddingService", "ClusteringService"]