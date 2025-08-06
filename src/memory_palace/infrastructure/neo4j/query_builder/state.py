"""State management for Cypher query builder.

This module provides state tracking to ensure semantically valid Cypher queries.
"""

from enum import Enum, auto
from typing import ClassVar


class ClauseType(Enum):
    """Enum for Cypher clause types."""

    # Core clauses
    MATCH = auto()
    OPTIONAL_MATCH = auto()
    WHERE = auto()
    RETURN = auto()
    WITH = auto()

    # Data manipulation
    CREATE = auto()
    MERGE = auto()
    DELETE = auto()
    DETACH_DELETE = auto()
    SET = auto()
    REMOVE = auto()

    # Pagination
    SKIP = auto()
    LIMIT = auto()

    # Ordering
    ORDER_BY = auto()

    # Miscellaneous
    CALL = auto()
    UNION = auto()
    UNWIND = auto()


class CypherQueryState:
    """State machine for tracking Cypher query state.

    This class ensures that clauses are added in a semantically valid order
    and prevents common issues like adding WHERE before MATCH or adding
    multiple RETURN clauses.
    """

    # Define valid clause sequences
    _VALID_AFTER: ClassVar[dict[ClauseType, set[ClauseType]]] = {
        # After MATCH, we can have WHERE, WITH, RETURN, or another MATCH
        ClauseType.MATCH: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After OPTIONAL_MATCH, same as MATCH
        ClauseType.OPTIONAL_MATCH: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After WHERE, we can have WITH, RETURN, or another data operation
        ClauseType.WHERE: {
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After RETURN, we can only have ORDER BY, SKIP, or LIMIT
        ClauseType.RETURN: {
            ClauseType.ORDER_BY,
            ClauseType.SKIP,
            ClauseType.LIMIT,
            ClauseType.UNION,
        },
        # After WITH, we can start a new query part
        ClauseType.WITH: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
            ClauseType.ORDER_BY,
        },
        # After CREATE or MERGE, we can have another clause or return
        ClauseType.CREATE: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        ClauseType.MERGE: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After DELETE or SET, we can move on
        ClauseType.DELETE: {
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        ClauseType.DETACH_DELETE: {
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        ClauseType.SET: {
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        ClauseType.REMOVE: {
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After ORDER BY, can have SKIP or LIMIT
        ClauseType.ORDER_BY: {ClauseType.SKIP, ClauseType.LIMIT},
        # After SKIP, can have LIMIT
        ClauseType.SKIP: {ClauseType.LIMIT},
        # After LIMIT, nothing more
        ClauseType.LIMIT: set(),
        # After UNION, can start a new subquery
        ClauseType.UNION: {ClauseType.MATCH, ClauseType.OPTIONAL_MATCH},
        # After CALL, can proceed with anything
        ClauseType.CALL: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
        # After UNWIND, can do most things
        ClauseType.UNWIND: {
            ClauseType.MATCH,
            ClauseType.OPTIONAL_MATCH,
            ClauseType.WHERE,
            ClauseType.WITH,
            ClauseType.RETURN,
            ClauseType.CREATE,
            ClauseType.MERGE,
            ClauseType.DELETE,
            ClauseType.DETACH_DELETE,
            ClauseType.SET,
            ClauseType.REMOVE,
            ClauseType.CALL,
            ClauseType.UNWIND,
        },
    }

    # Valid start clauses
    _VALID_START_CLAUSES: ClassVar[set[ClauseType]] = {
        ClauseType.MATCH,
        ClauseType.OPTIONAL_MATCH,
        ClauseType.CREATE,
        ClauseType.MERGE,
        ClauseType.CALL,
        ClauseType.UNWIND,
    }

    # Clauses that can appear only once in a single query segment (between WITH clauses)
    _ONCE_PER_SEGMENT: ClassVar[set[ClauseType]] = {ClauseType.RETURN}

    def __init__(self) -> None:
        """Initialize the Cypher query state."""
        self._clauses: list[ClauseType] = []
        self._current_segment: list[ClauseType] = []
        self._is_complete = False

    @property
    def is_complete(self) -> bool:
        """Check if the query is complete."""
        return self._is_complete

    @property
    def current_clause(self) -> ClauseType | None:
        """Get the current clause type."""
        if not self._clauses:
            return None
        return self._clauses[-1]

    def add_clause(self, clause_type: ClauseType) -> None:
        """Add a clause to the query state.

        Args:
            clause_type: The type of clause to add

        Raises:
            ValueError: If adding the clause would create an invalid query
        """
        # If this is the first clause, it must be a valid start clause
        if not self._clauses and clause_type not in self._VALID_START_CLAUSES:
            valid_starts = ", ".join(str(clause) for clause in self._VALID_START_CLAUSES)
            raise ValueError(f"Query must start with one of: {valid_starts}, got {clause_type}")

        # If not the first clause, check if it's valid after the previous clause
        elif self._clauses:
            prev_clause: ClauseType = self._clauses[-1]
            if clause_type not in self._VALID_AFTER.get(prev_clause, set[ClauseType]()):
                valid_next = ", ".join(
                    str(clause) for clause in self._VALID_AFTER.get(prev_clause, set[ClauseType]())
                )
                raise ValueError(
                    f"Cannot add {clause_type} after {prev_clause}, valid options are: {valid_next}"
                )

        # Handle segment tracking (segments are separated by WITH clauses)
        if clause_type == ClauseType.WITH:
            # Reset the segment tracking
            self._current_segment = []
        else:
            # Check for clauses that should only appear once per segment
            if clause_type in self._ONCE_PER_SEGMENT and clause_type in self._current_segment:
                raise ValueError(f"{clause_type} can only appear once per query segment")

            self._current_segment.append(clause_type)

        # Add the clause to the state
        self._clauses.append(clause_type)

        # Update completion status
        self._update_completion_status()

    def _update_completion_status(self) -> None:
        """Update whether the query is in a complete state."""
        # A query is complete if it ends with RETURN (most common case)
        if (
            (self._clauses and self._clauses[-1] == ClauseType.RETURN)
            or (
                len(self._clauses) >= 2
                and ClauseType.RETURN in self._clauses
                and self._clauses[-1] in {ClauseType.LIMIT, ClauseType.SKIP, ClauseType.ORDER_BY}
            )
            or (
                self._clauses
                and self._clauses[-1]
                in {
                    ClauseType.DELETE,
                    ClauseType.DETACH_DELETE,
                    ClauseType.CREATE,
                    ClauseType.MERGE,
                    ClauseType.SET,
                    ClauseType.REMOVE,
                }
            )
        ):
            self._is_complete = True
        else:
            self._is_complete = False

    def validate_can_add_match(self) -> None:
        """Validate that a MATCH clause can be added."""
        self._validate_can_add(clause_type=ClauseType.MATCH)

    def validate_can_add_where(self) -> None:
        """Validate that a WHERE clause can be added."""
        self._validate_can_add(clause_type=ClauseType.WHERE)

    def validate_can_add_return(self) -> None:
        """Validate that a RETURN clause can be added."""
        self._validate_can_add(clause_type=ClauseType.RETURN)

    def validate_can_add_with(self) -> None:
        """Validate that a WITH clause can be added."""
        self._validate_can_add(clause_type=ClauseType.WITH)

    def validate_can_add_create(self) -> None:
        """Validate that a CREATE clause can be added."""
        self._validate_can_add(clause_type=ClauseType.CREATE)

    def validate_can_add_merge(self) -> None:
        """Validate that a MERGE clause can be added."""
        self._validate_can_add(clause_type=ClauseType.MERGE)

    def validate_can_add_delete(self) -> None:
        """Validate that a DELETE clause can be added."""
        self._validate_can_add(clause_type=ClauseType.DELETE)

    def validate_can_add_set(self) -> None:
        """Validate that a SET clause can be added."""
        self._validate_can_add(clause_type=ClauseType.SET)

    def validate_can_add_order_by(self) -> None:
        """Validate that an ORDER BY clause can be added."""
        self._validate_can_add(clause_type=ClauseType.ORDER_BY)

    def validate_can_add_skip(self) -> None:
        """Validate that a SKIP clause can be added."""
        self._validate_can_add(clause_type=ClauseType.SKIP)

    def validate_can_add_limit(self) -> None:
        """Validate that a LIMIT clause can be added."""
        self._validate_can_add(clause_type=ClauseType.LIMIT)

    def _validate_can_add(self, clause_type: ClauseType) -> None:
        """Validate that a clause can be added.

        Args:
            clause_type: The type of clause to validate

        Raises:
            ValueError: If the clause cannot be added
        """
        # Check if this is the first clause
        if not self._clauses:
            if clause_type not in self._VALID_START_CLAUSES:
                valid_starts = ", ".join(str(clause) for clause in self._VALID_START_CLAUSES)
                raise ValueError(f"Query must start with one of: {valid_starts}, got {clause_type}")
            return

        # Check if it's valid after the previous clause
        prev_clause: ClauseType = self._clauses[-1]
        if clause_type not in self._VALID_AFTER.get(prev_clause, set[ClauseType]()):
            valid_next = ", ".join(
                str(clause) for clause in self._VALID_AFTER.get(prev_clause, set[ClauseType]())
            )
            raise ValueError(
                f"Cannot add {clause_type} after {prev_clause}, valid options are: {valid_next}"
            )

        # Check for clauses that should only appear once per segment
        if clause_type in self._ONCE_PER_SEGMENT and clause_type in self._current_segment:
            raise ValueError(f"{clause_type} can only appear once per query segment")

    def validate_query_complete(self) -> None:
        """Validate that the query is in a complete state.

        Raises:
            ValueError: If the query is not complete
        """
        if not self._is_complete:
            raise ValueError(
                "Query is not complete. It must end with RETURN or a write operation "
                "(CREATE, MERGE, DELETE, SET, etc.)"
            )
