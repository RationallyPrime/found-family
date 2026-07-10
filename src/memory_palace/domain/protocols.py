"""Canonical domain service protocols."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingService(Protocol):
    """One swappable contract for every embedding provider implementation."""

    model: str

    async def embed_text(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    def get_model_dimensions(self) -> int: ...


@runtime_checkable
class ClusteringService(Protocol):
    """Swappable contract for clustering strategies."""

    async def predict(self, embeddings: list[list[float]]) -> list[int]: ...

    async def fit(self, embeddings: list[list[float]]) -> None: ...

    async def reset(self) -> None: ...


# Compatibility name for callers of the original module; this is an alias,
# not a second structural contract.
EmbeddingServiceProtocol = EmbeddingService

__all__ = ["ClusteringService", "EmbeddingService", "EmbeddingServiceProtocol"]
