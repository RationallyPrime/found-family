"""Neo4j query builder framework.

This package provides a type-safe, fluent interface for building Cypher queries.
"""

from .builder import CypherQueryBuilder
from .pagination import PaginationMixin
from .patterns import NodePattern, PatternBuilder, RelationshipPattern
from .state import ClauseType, CypherQueryState
from .vector import VectorSearchMixin

__all__ = [
    "ClauseType",
    # Base query builder
    "CypherQueryBuilder",
    "CypherQueryState",
    # Patterns
    "NodePattern",
    # Mixins
    "PaginationMixin",
    "PatternBuilder",
    "RelationshipPattern",
    "VectorSearchMixin",
]
