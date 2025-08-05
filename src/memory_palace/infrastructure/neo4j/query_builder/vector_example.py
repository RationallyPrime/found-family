"""Examples of vector search with the Cypher query builder.

This file demonstrates how to use the VectorSearchMixin to
construct vector search queries with Neo4j Community edition.
"""

import random
from typing import LiteralString, cast

from pydantic import BaseModel

from memory_palace.infrastructure.neo4j.query_builder import (
    CypherQueryBuilder,
    PaginationMixin,
    VectorSearchMixin,
)


# Example Pydantic model for vector search results
class DocumentEmbedding(BaseModel):
    """Sample document embedding model for demonstration."""

    id: str
    text: str
    embedding: list[float]
    similarity: float


# Example of a query builder with both vector search and pagination
class VectorQueryBuilder(
    CypherQueryBuilder[DocumentEmbedding],
    VectorSearchMixin[DocumentEmbedding],
    PaginationMixin[DocumentEmbedding],
):
    """Sample query builder with vector search and pagination support."""

    # Fix return type mismatches by overriding methods to return self
    def skip(self, count: int) -> "VectorQueryBuilder":
        """Override to fix return type."""
        super().skip(count)
        return self

    def limit(self, count: int) -> "VectorQueryBuilder":
        """Override to fix return type."""
        super().limit(count)
        return self

    def paginate(self, page: int, page_size: int) -> "VectorQueryBuilder":
        """Override to fix return type."""
        super().paginate(page, page_size)
        return self


def generate_random_vector(dimensions: int = 384) -> list[float]:
    """Generate a random vector for demonstration purposes."""
    return [random.uniform(-1.0, 1.0) for _ in range(dimensions)]


def example_community_vector_search() -> None:
    """Example of vector search with Neo4j Community."""
    # Create a query builder
    query: VectorQueryBuilder = VectorQueryBuilder()

    # Create a random query vector
    query_vector: list[float] = generate_random_vector(dimensions=384)

    # Build a query to find similar documents
    builder: VectorSearchMixin[DocumentEmbedding] = query.community_vector_search(
        property_name="embedding",
        vector=query_vector,
        k=5,
        similarity_function="cosine",
        similarity_cutoff=0.7,
        node_label="Document",
        node_variable="doc",
    )

    # Add return clause
    # We need to safely pass the builder to another instance
    new_builder: CypherQueryBuilder[DocumentEmbedding] = CypherQueryBuilder[DocumentEmbedding]()

    # Copy the state using interface methods
    # No need to check instanceof as CypherQueryBuilder always implements QueryBuilderInterface
    # Copy needed state (this would be better done with a dedicated method)
    for part in getattr(builder, "_query_parts", []):
        new_builder.append_query_part(part)

    # Copy parameters and counter
    if hasattr(builder, "_parameters"):
        parameters: dict[str, object] = getattr(builder, "_parameters", {})
        for _key, value in parameters.items():
            new_builder.add_parameter(value)

    # Now we can add the return clause
    builder_with_query: CypherQueryBuilder[DocumentEmbedding] = new_builder.return_clause(
        cast("LiteralString", "doc"), cast("LiteralString", "similarity")
    )
    query_str, params = builder_with_query.build()

    print("Example Community Vector Search:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_vector_search() -> None:
    """Example of using the standard vector search method."""
    # Create a query builder
    query: VectorQueryBuilder = VectorQueryBuilder()

    # Create a random query vector
    query_vector: list[float] = generate_random_vector(384)

    # Build a query to find similar documents using the auto-selecting method
    builder: VectorSearchMixin[DocumentEmbedding] = query.vector_search(
        property_name="embedding",
        vector=query_vector,
        k=5,
        similarity_function="cosine",
        node_label="Document",
    )

    # Add return clause
    # We need to safely pass the builder to another instance
    new_builder: CypherQueryBuilder[DocumentEmbedding] = CypherQueryBuilder[DocumentEmbedding]()

    # Copy the state using interface methods
    # No need to check instanceof as CypherQueryBuilder always implements QueryBuilderInterface
    # Copy needed state (this would be better done with a dedicated method)
    for part in getattr(builder, "_query_parts", []):
        new_builder.append_query_part(part)

    # Copy parameters and counter
    if hasattr(builder, "_parameters"):
        parameters: dict[str, object] = getattr(builder, "_parameters", {})
        for _key, value in parameters.items():
            new_builder.add_parameter(value)

    # Now we can add the return clause
    builder_with_query: CypherQueryBuilder[DocumentEmbedding] = new_builder.return_clause(
        cast("LiteralString", "n"), cast("LiteralString", "similarity")
    )
    query_str, params = builder_with_query.build()

    print("\nExample Vector Search:")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def example_hybrid_search() -> None:
    """Example of hybrid vector + graph search (GraphRAG)."""
    # Create a query builder
    query: VectorQueryBuilder = VectorQueryBuilder()

    # Create a random query vector
    query_vector: list[float] = generate_random_vector(384)

    # Build a query to find similar documents and traverse relationships
    builder: VectorSearchMixin[DocumentEmbedding] = query.vector_search_hybrid(
        property_name="embedding",
        vector=query_vector,
        k=5,
        expansion_factor=10,
        path_pattern="(candidate)-[:CITES]->(result:Document)",
        rerank=True,
    )

    # Add return clause
    # We need to safely pass the builder to another instance
    new_builder: CypherQueryBuilder[DocumentEmbedding] = CypherQueryBuilder[DocumentEmbedding]()

    # Copy the state using interface methods
    # No need to check instanceof as CypherQueryBuilder always implements QueryBuilderInterface
    # Copy needed state (this would be better done with a dedicated method)
    for part in getattr(builder, "_query_parts", []):
        new_builder.append_query_part(part)

    # Copy parameters and counter
    if hasattr(builder, "_parameters"):
        parameters: dict[str, object] = getattr(builder, "_parameters", {})
        for _key, value in parameters.items():
            new_builder.add_parameter(value)

    # Now we can add the return clause
    builder_with_query: CypherQueryBuilder[DocumentEmbedding] = new_builder.return_clause(
        cast("LiteralString", "result"), cast("LiteralString", "similarity")
    )
    query_str, params = builder_with_query.build()

    print("\nExample Hybrid Search (GraphRAG):")
    print(f"Query: {query_str}")
    print(f"Params: {params}")


def run_all_examples() -> None:
    """Run all example queries."""
    example_community_vector_search()
    example_vector_search()
    example_hybrid_search()


if __name__ == "__main__":
    # This file is not meant to be run directly, but if it is, show the examples
    run_all_examples()
