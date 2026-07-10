"""Curated imports preserve embedding provenance across idempotent MERGEs."""

from memory_palace.domain.models.memories import Consolidation
from memory_palace.infrastructure.embeddings.provenance import attach_embedding_provenance


class StubEmbeddingService:
    model = "voyage-test"

    async def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def get_model_dimensions(self) -> int:
        return 2


def test_embedding_attachment_overwrites_stale_vector_and_provenance() -> None:
    consolidation = Consolidation(
        content="curated",
        embedding=[0.0],
        embedding_model="old-model",
        embedding_dimensions=1,
    )

    attach_embedding_provenance(consolidation, [1.0, 0.0], StubEmbeddingService())

    assert consolidation.embedding == [1.0, 0.0]
    assert consolidation.embedding_model == "voyage-test"
    assert consolidation.embedding_dimensions == 2
