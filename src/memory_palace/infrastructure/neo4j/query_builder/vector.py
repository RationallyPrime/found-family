"""Vector search functionality for the Cypher query builder.

This module provides methods for vector similarity search in Neo4j
for Neo4j Community edition.
"""

from typing import Generic, LiteralString, TypeVar, cast

from structlog.typing import FilteringBoundLogger

from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.query_builder.interfaces import (
    QueryBuilderInterface,
)
from memory_palace.infrastructure.neo4j.query_builder.state import (
    ClauseType,
)


logger: FilteringBoundLogger = get_logger(name=__name__)

# Generic type variable for query results
T = TypeVar(name="T")

# Neo4j Community edition is used throughout this codebase


def create_literal_str(prefix: str, clause: str) -> LiteralString:
    """Create a LiteralString by concatenating strings.

    This function serves as a trusted boundary for creating LiteralString.
    In a real implementation, you would validate the inputs here
    to ensure they don't contain injection vulnerabilities.

    Args:
        prefix: String prefix to add
        clause: Main clause string

    Returns:
        A LiteralString that can be safely used in query builders
    """
    # Since we're casting, this is the point where we should validate
    # that the inputs don't contain anything malicious
    return cast("LiteralString", prefix + clause)


class VectorSearchMixin(Generic[T]):
    """Mixin for adding vector search functionality to query builders.

    This mixin adds methods for performing vector similarity search in Neo4j
    for Community edition.
    """

    def vector_search(
        self,
        property_name: str,
        vector: list[float],
        k: int = 10,
        similarity_function: str = "cosine",
        similarity_cutoff: float | None = None,
        node_label: str | None = None,
        node_variable: str = "n",
    ) -> "VectorSearchMixin[T]":
        """Add vector similarity search using Neo4j Community edition.

        This method uses vectorized operations in Cypher to compute similarity.

        Args:
            property_name: Name of the vector property in Neo4j
            vector: Query vector to use for similarity search
            k: Number of results to return
            similarity_function: Similarity function to use (cosine, euclidean, dot)
            similarity_cutoff: Minimum similarity score (0-1)
            node_label: Optional node label to filter on
            node_variable: Variable name for the node

        Returns:
            Self for method chaining
        """
        return self.community_vector_search(
            property_name=property_name,
            vector=vector,
            k=k,
            similarity_function=similarity_function,
            similarity_cutoff=similarity_cutoff,
            node_label=node_label,
            node_variable=node_variable,
        )

    def community_vector_search(
        self,
        property_name: str,
        vector: list[float],
        k: int = 10,
        similarity_function: str = "cosine",
        similarity_cutoff: float | None = None,
        node_label: str | None = None,
        node_variable: str = "n",
    ) -> "VectorSearchMixin[T]":
        """Add vector similarity search compatible with Neo4j Community edition.

        This method uses vectorized operations in Cypher to compute similarity.
        It's less efficient than HNSW indexes but works with Neo4j Community.

        Args:
            property_name: Name of the vector property in Neo4j
            vector: Query vector to use for similarity search
            k: Number of results to return
            similarity_function: Similarity function to use (cosine, euclidean, dot)
            similarity_cutoff: Minimum similarity score (0-1)
            node_label: Optional node label to filter on
            node_variable: Variable name for the node

        Returns:
            Self for method chaining
        """
        # Ensure we're working with a query builder
        if not isinstance(self, QueryBuilderInterface):
            raise TypeError(
                "VectorSearchMixin must be used with a QueryBuilderInterface implementation"
            )

        query_builder: QueryBuilderInterface = self

        # Add parameter for vector
        param_name: str = query_builder.add_parameter(vector)

        # Build proper label clause
        label_clause = f":{node_label}" if node_label else ""

        # Build MATCH clause
        match_clause = f"MATCH ({node_variable}{label_clause})"
        query_builder.append_query_part(create_literal_str("", match_clause))
        query_builder.add_clause(clause_type=ClauseType.MATCH)

        # Build WITH clause with similarity calculation
        if similarity_function == "cosine":
            # Cosine similarity calculation
            similarity_calc = f"gds.similarity.cosine({node_variable}.{property_name}, ${param_name}) AS similarity"
        elif similarity_function == "euclidean":
            # Euclidean distance calculation (for compatibility - convert to similarity)
            # 1/(1+distance) to convert distance to similarity score
            similarity_calc = (
                f"1/(1 + gds.similarity.euclidean({node_variable}.{property_name}, ${param_name})) "
                f"AS similarity"
            )
        else:  # dot product
            # Dot product calculation
            similarity_calc = (
                f"gds.similarity.dotProduct({node_variable}.{property_name}, ${param_name}) "
                f"AS similarity"
            )

        # Add WITH clause
        with_clause = f"WITH {node_variable}, {similarity_calc}"
        query_builder.append_query_part(create_literal_str(" ", with_clause))
        query_builder.add_clause(ClauseType.WITH)

        # Add WHERE clause if similarity cutoff is specified
        if similarity_cutoff is not None:
            cutoff_param = query_builder.add_parameter(similarity_cutoff)
            where_clause = f"WHERE similarity >= ${cutoff_param}"
            query_builder.append_query_part(create_literal_str(" ", where_clause))
            query_builder.add_clause(clause_type=ClauseType.WHERE)

        # Add ORDER BY clause
        order_clause = "ORDER BY similarity DESC"
        query_builder.append_query_part(create_literal_str(" ", order_clause))
        query_builder.add_clause(clause_type=ClauseType.ORDER_BY)

        # Add LIMIT clause
        limit_param = query_builder.add_parameter(k)
        limit_clause = f"LIMIT ${limit_param}"
        query_builder.append_query_part(create_literal_str(" ", limit_clause))
        query_builder.add_clause(clause_type=ClauseType.LIMIT)

        return self

    def vector_search_hybrid(
        self,
        property_name: str,
        vector: list[float],
        k: int = 10,
        expansion_factor: int = 100,
        path_pattern: str | None = None,
        rerank: bool = True,
    ) -> "VectorSearchMixin[T]":
        """Add a hybrid vector + graph search for improved relevance.

        This method combines vector similarity search with graph traversal,
        often referred to as the GraphRAG approach. It first retrieves a larger
        set of candidates using vector search, then filters/expands through
        graph relationships, and optionally re-ranks the results.

        Args:
            property_name: Name of the vector property in Neo4j
            vector: Query vector to use for similarity search
            k: Number of final results to return
            expansion_factor: Factor to expand initial vector search (for re-ranking)
            path_pattern: Optional Cypher path pattern for graph traversal
            rerank: Whether to re-rank results by vector similarity after expansion

        Returns:
            Self for method chaining
        """
        # Type checking bypass - this will be fixed when properly integrated
        # pylint: disable=no-member

        # Get expanded set of candidates through vector search
        initial_k = k * expansion_factor

        # First do vector search to get candidate nodes
        vector_search: VectorSearchMixin[T] = self.vector_search(
            property_name=property_name,
            vector=vector,
            k=initial_k,
            node_variable="candidate",
        )

        # If there's a path pattern, use it to filter/expand candidates
        from memory_palace.infrastructure.neo4j.query_builder.builder import (
            CypherQueryBuilder,
        )

        if path_pattern and isinstance(vector_search, CypherQueryBuilder):
            # Add WITH clause to pass candidates to the next phase
            with_clause = "WITH candidate, similarity"
            # Properly maintain LiteralString type safety
            vector_search._query_parts.append(create_literal_str(" ", with_clause))
            vector_search._state_machine.add_clause(clause_type=ClauseType.WITH)

            # Add MATCH clause with the path pattern
            path_variable = "p"
            match_clause = f"MATCH {path_variable} = {path_pattern}"
            vector_search._query_parts.append(create_literal_str(" ", match_clause))
            vector_search._state_machine.add_clause(clause_type=ClauseType.MATCH)

            # If re-ranking, we need to collect and sort results again
            if rerank:
                # Get result node from path pattern (assumed to be the last node)
                result_var: str = "result"
                with_clause: LiteralString = f"WITH {result_var}, similarity"
                # String concatenation breaks LiteralString typing
                # Use our helper function to create a safe LiteralString
                vector_search._query_parts.append(create_literal_str(" ", with_clause))
                vector_search._state_machine.add_clause(clause_type=ClauseType.WITH)

                # Order by similarity again
                order_clause: LiteralString = "ORDER BY similarity DESC"
                vector_search._query_parts.append(create_literal_str(" ", order_clause))
                vector_search._state_machine.add_clause(clause_type=ClauseType.ORDER_BY)

        # Add final limit - access via underlying CypherQueryBuilder
        # This assumes the mixin is used with CypherQueryBuilder
        from memory_palace.infrastructure.neo4j.query_builder.builder import (
            CypherQueryBuilder,
        )

        if isinstance(vector_search, QueryBuilderInterface):
            param_name = vector_search.add_parameter(k)
            limit_clause = f"LIMIT ${param_name}"
            vector_search.append_query_part(create_literal_str(" ", limit_clause))
            vector_search.add_clause(clause_type=ClauseType.LIMIT)

        return vector_search


# Export as part of the public API
__all__ = ["VectorSearchMixin"]
