"""Pagination mixin for the Cypher query builder.

This module provides a separate mixin for pagination operations
to keep the core query builder clean.
"""

from typing import Generic, TypeVar

from memory_palace.infrastructure.neo4j.query_builder.state import (
    ClauseType,
)

T = TypeVar("T")


class PaginationMixin(Generic[T]):
    """Mixin for adding pagination functionality to query builders.

    This mixin adds skip and limit methods to provide pagination
    capabilities. It can be mixed into any query builder class
    that has the necessary internal structure.

    It's separated from the core builder to keep concerns separated
    and follow the recommendation from the feedback document.
    """

    def skip(self, count: int) -> "PaginationMixin[T]":
        """Add a SKIP clause to the query.

        Args:
            count: Number of results to skip

        Returns:
            Self for method chaining
        """
        # Type checking bypass - this will be fixed when properly integrated
        # pylint: disable=no-member
        self._state_machine.validate_can_add_skip()  # type: ignore

        # Add parameter for skip count
        # pylint: disable=no-member
        param_name = self._add_parameter(count)  # type: ignore

        # Add to query parts
        # pylint: disable=no-member
        self._query_parts.append(f"SKIP ${param_name}")  # type: ignore

        # Update state
        # pylint: disable=no-member
        self._state_machine.add_clause(ClauseType.SKIP)  # type: ignore

        return self

    def limit(self, count: int) -> "PaginationMixin[T]":
        """Add a LIMIT clause to the query.

        Args:
            count: Maximum number of results to return

        Returns:
            Self for method chaining
        """
        # Type checking bypass - this will be fixed when properly integrated
        # pylint: disable=no-member
        self._state_machine.validate_can_add_limit()  # type: ignore

        # Add parameter for limit count
        # pylint: disable=no-member
        param_name = self._add_parameter(count)  # type: ignore

        # Add to query parts
        # pylint: disable=no-member
        self._query_parts.append(f"LIMIT ${param_name}")  # type: ignore

        # Update state
        # pylint: disable=no-member
        self._state_machine.add_clause(ClauseType.LIMIT)  # type: ignore

        return self

    def paginate(self, page: int, page_size: int) -> "PaginationMixin[T]":
        """Add pagination (SKIP and LIMIT) based on page number and size.

        This is a convenience method that combines skip and limit.

        Args:
            page: Page number (1-based)
            page_size: Number of items per page

        Returns:
            Self for method chaining
        """
        if page < 1:
            raise ValueError("Page number must be greater than or equal to 1")

        if page_size < 1:
            raise ValueError("Page size must be greater than or equal to 1")

        # Calculate skip value based on page number
        skip_count = (page - 1) * page_size

        return self.skip(skip_count).limit(page_size)
