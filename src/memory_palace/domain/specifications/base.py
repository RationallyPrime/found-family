"""Base specification interfaces and implementations.

This module defines the core specification pattern interfaces that enable
building reusable, composable query predicates for domain entities.
"""

from typing import Any, Protocol, TypeVar

T = TypeVar("T")  # Entity type


class Specification(Protocol[T]):
    """Protocol for specifications that can evaluate entities and convert to filters.

    Specifications encapsulate business rules and can be composed together
    to build complex queries in a reusable way.
    """

    def is_satisfied_by(self, entity: T) -> bool:
        """Check if the entity satisfies this specification.

        Args:
            entity: The entity to evaluate

        Returns:
            True if the entity satisfies the specification
        """
        ...

    def to_filter(self) -> dict[str, Any]:
        """Convert this specification to a filter dict for repositories.

        Returns:
            Filter dictionary that can be used in repository queries
        """
        ...

    def and_(self, other: "Specification[T]") -> "Specification[T]":
        """Combine with another specification using AND logic.

        Args:
            other: Another specification to combine with

        Returns:
            A new specification that requires both to be satisfied
        """
        ...

    def or_(self, other: "Specification[T]") -> "Specification[T]":
        """Combine with another specification using OR logic.

        Args:
            other: Another specification to combine with

        Returns:
            A new specification that requires either to be satisfied
        """
        ...

    def not_(self) -> "Specification[T]":
        """Negate this specification.

        Returns:
            A new specification that is satisfied when this one is not
        """
        ...


class AndSpecification:
    """Specification that requires all sub-specifications to be satisfied."""

    def __init__(self, *specs: Specification[Any]) -> None:
        """Initialize with specifications to combine with AND logic.

        Args:
            *specs: Specifications that all must be satisfied
        """
        self.specs = specs

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if all specifications are satisfied."""
        return all(spec.is_satisfied_by(entity) for spec in self.specs)

    def to_filter(self) -> dict[str, Any]:
        """Combine filters with AND logic."""
        if not self.specs:
            return {}

        # For a simple implementation, merge all filters
        # In practice, this might need more sophisticated handling
        result = {}
        for spec in self.specs:
            filter_dict = spec.to_filter()
            for key, value in filter_dict.items():
                if key in result:
                    # Handle conflicting keys - this is a simple approach
                    if isinstance(result[key], list):
                        result[key].append(value)
                    else:
                        result[key] = [result[key], value]
                else:
                    result[key] = value
        return result

    def and_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with AND logic."""
        return AndSpecification(self, other)

    def or_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with OR logic."""
        return OrSpecification(self, other)

    def not_(self) -> Specification[Any]:
        """Negate this specification."""
        return NotSpecification(self)


class OrSpecification:
    """Specification that requires at least one sub-specification to be satisfied."""

    def __init__(self, *specs: Specification[Any]) -> None:
        """Initialize with specifications to combine with OR logic.

        Args:
            *specs: Specifications where at least one must be satisfied
        """
        self.specs = specs

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if any specification is satisfied."""
        return any(spec.is_satisfied_by(entity) for spec in self.specs)

    def to_filter(self) -> dict[str, Any]:
        """Combine filters with OR logic."""
        if not self.specs:
            return {}

        # Return a special structure that repositories can interpret as OR
        return {"$or": [spec.to_filter() for spec in self.specs]}

    def and_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with AND logic."""
        return AndSpecification(self, other)

    def or_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with OR logic."""
        return OrSpecification(self, other)

    def not_(self) -> Specification[Any]:
        """Negate this specification."""
        return NotSpecification(self)


class NotSpecification:
    """Specification that inverts another specification."""

    def __init__(self, spec: Specification[Any]) -> None:
        """Initialize with a specification to negate.

        Args:
            spec: The specification to negate
        """
        self.spec = spec

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the specification is NOT satisfied."""
        return not self.spec.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Create a negated filter."""
        return {"$not": self.spec.to_filter()}

    def and_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with AND logic."""
        return AndSpecification(self, other)

    def or_(self, other: Specification[Any]) -> Specification[Any]:
        """Combine with OR logic."""
        return OrSpecification(self, other)

    def not_(self) -> Specification[Any]:
        """Negate this specification."""
        return NotSpecification(self)
