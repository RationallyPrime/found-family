"""Domain service protocols."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingServiceProtocol(Protocol):
    """Protocol for embedding services."""

    async def embed(self, texts: list[str], embedding_type: str) -> list[list[float]]:
        """Generate embeddings for the given texts."""
        ...
