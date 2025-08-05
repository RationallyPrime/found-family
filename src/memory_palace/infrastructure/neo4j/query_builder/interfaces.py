"""Query builder interfaces for dependency injection.

This module defines interfaces for query builders to support dependency injection
and decouple mixins from concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import Any, LiteralString, Protocol, TypeVar

from memory_palace.infrastructure.neo4j.query_builder.state import (
    ClauseType,
)

# Generic type variable for query results
T = TypeVar("T")


class QueryPartAppender(Protocol):
    """Protocol for appending query parts to a query builder."""

    def append_query_part(self, part: LiteralString) -> None:
        """Append a query part to the query being built.

        Args:
            part: The query part to append (must be a LiteralString for safety)
        """
        ...


class StateManagement(Protocol):
    """Protocol for query state management."""

    def add_clause(self, clause_type: ClauseType) -> None:
        """Add a clause to the query state.

        Args:
            clause_type: The type of clause being added
        """
        ...


class ParameterManagement(Protocol):
    """Protocol for query parameter management."""

    def add_parameter(self, value: Any) -> str:
        """Add a parameter to the query.

        Args:
            value: The parameter value to add

        Returns:
            Parameter name to use in the query
        """
        ...


class QueryBuilderInterface(QueryPartAppender, StateManagement, ParameterManagement, ABC):
    """Interface for query builders with standard methods required by mixins."""

    @abstractmethod
    def append_query_part(self, part: LiteralString) -> None:
        """Append a query part to the query being built.

        Args:
            part: The query part to append (must be a LiteralString for safety)
        """
        pass

    @abstractmethod
    def add_clause(self, clause_type: ClauseType) -> None:
        """Add a clause to the query state.

        Args:
            clause_type: The type of clause being added
        """
        pass

    @abstractmethod
    def add_parameter(self, value: Any) -> str:
        """Add a parameter to the query.

        Args:
            value: The parameter value to add

        Returns:
            Parameter name to use in the query
        """
        pass


class QueryBuilder(QueryBuilderInterface, ABC):
    """Base abstract class for query builders with standard functionality."""

    @abstractmethod
    def build(self) -> tuple[LiteralString, dict[str, Any]]:
        """Build the final query and parameters.

        Returns:
            Tuple of (query, params)
        """
        pass
