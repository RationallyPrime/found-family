"""Similarity-based specification for vector search.

This specification enables semantic similarity search to be composed
with other specifications in a unified filtering system.
"""

from typing import Any

from memory_palace.domain.specifications.composite import BaseSpecification


class SimilaritySpecification(BaseSpecification):
    """Specification for vector similarity search.

    This allows similarity search to be combined with other filters
    using the standard specification pattern.
    """

    def __init__(self, embedding: list[float], threshold: float = 0.7, alias: str = "m"):
        """Initialize similarity specification.

        Args:
            embedding: Query embedding vector
            threshold: Minimum similarity threshold (0.0-1.0)
            alias: Node alias in the query (default "m")
        """
        self.embedding = embedding
        self.threshold = threshold
        self.alias = alias
        self._embedding_param = None  # Will be set when building query
        self._threshold_param = None

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if entity has sufficient similarity.

        This requires the entity to have an embedding attribute
        and computes cosine similarity.
        """
        if not hasattr(entity, "embedding"):
            return False

        entity_embedding = entity.embedding
        if not entity_embedding or len(entity_embedding) != len(self.embedding):
            return False

        # Compute cosine similarity
        dot_product = sum(a * b for a, b in zip(self.embedding, entity_embedding, strict=False))
        norm_a = sum(a * a for a in self.embedding) ** 0.5
        norm_b = sum(b * b for b in entity_embedding) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return False

        similarity = dot_product / (norm_a * norm_b)
        return similarity >= self.threshold

    def to_filter(self) -> dict[str, Any]:
        """Convert to filter dict.

        Note: This returns a special marker that repositories
        can recognize to trigger similarity search.
        """
        return {"$similarity": {"embedding": self.embedding, "threshold": self.threshold}}

    def to_cypher(self, params: dict[str, Any] | None = None) -> str:
        """Generate Cypher for similarity filtering.

        This needs to be applied AFTER a WITH clause that calculates similarity.
        The actual similarity calculation should be done separately.

        Args:
            params: Optional parameter dict to populate with embedding/threshold

        Returns:
            Cypher WHERE condition for similarity threshold
        """
        if params is not None:
            # Store parameters for the query
            params["query_embedding"] = self.embedding
            params["similarity_threshold"] = self.threshold

        # Return the WHERE condition that filters by similarity
        # This assumes similarity has been calculated in a prior WITH clause
        return f"similarity > {self.threshold}"

    def requires_similarity_calculation(self) -> bool:
        """Indicates this specification needs similarity calculation."""
        return True

    def get_similarity_calculation(self, alias: str | None = None) -> str:
        """Get the Cypher expression for calculating similarity.

        Args:
            alias: Node alias to use (defaults to self.alias)

        Returns:
            Cypher expression for cosine similarity calculation
        """
        node_alias = alias or self.alias

        return f"""
        reduce(dot = 0.0, i IN range(0, size({node_alias}.embedding)-1) | 
               dot + {node_alias}.embedding[i] * $query_embedding[i]) / 
        (sqrt(reduce(sum = 0.0, i IN range(0, size({node_alias}.embedding)-1) | 
               sum + {node_alias}.embedding[i] * {node_alias}.embedding[i])) * 
         sqrt(reduce(sum = 0.0, i IN range(0, size($query_embedding)-1) | 
               sum + $query_embedding[i] * $query_embedding[i])))
        """.strip()
