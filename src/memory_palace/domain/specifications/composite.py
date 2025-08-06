"""Composite specification implementations.

This module provides base classes for building composite specifications
that can be extended for specific domain needs.
"""

from typing import Any, TypeVar

T = TypeVar("T")


class BaseSpecification:
    """Base class for concrete specifications.

    This provides default implementations of the composition methods
    so that concrete specifications only need to implement the core logic.
    """

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the entity satisfies this specification."""
        raise NotImplementedError("Subclasses must implement is_satisfied_by")

    def to_filter(self) -> dict[str, Any]:
        """Convert this specification to a filter dict."""
        return {}

    def and_(self, other: "BaseSpecification") -> "BaseSpecification":
        """Combine with another specification using AND logic."""
        return _AndSpecification(self, other)

    def or_(self, other: "BaseSpecification") -> "BaseSpecification":
        """Combine with another specification using OR logic."""
        return _OrSpecification(self, other)

    def not_(self) -> "BaseSpecification":
        """Negate this specification."""
        return _NotSpecification(self)


class _AndSpecification(BaseSpecification):
    """Internal AND specification implementation."""

    def __init__(self, left: BaseSpecification, right: BaseSpecification) -> None:
        self.left = left
        self.right = right

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if both specifications are satisfied."""
        return self.left.is_satisfied_by(entity) and self.right.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Combine filters with AND logic."""
        left_filter = self.left.to_filter()
        right_filter = self.right.to_filter()

        # Simple merge - in practice might need more sophisticated handling
        result = {}
        result.update(left_filter)
        result.update(right_filter)
        return result


class _OrSpecification(BaseSpecification):
    """Internal OR specification implementation."""

    def __init__(self, left: BaseSpecification, right: BaseSpecification) -> None:
        self.left = left
        self.right = right

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if either specification is satisfied."""
        return self.left.is_satisfied_by(entity) or self.right.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Combine filters with OR logic."""
        left_filter = self.left.to_filter()
        right_filter = self.right.to_filter()
        return {"$or": [left_filter, right_filter]}


class _NotSpecification(BaseSpecification):
    """Internal NOT specification implementation."""

    def __init__(self, spec: BaseSpecification) -> None:
        self.spec = spec

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the specification is NOT satisfied."""
        return not self.spec.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Create a negated filter."""
        return {"$not": self.spec.to_filter()}


class AttributeSpecification(BaseSpecification):
    """Specification that checks an attribute value."""

    def __init__(self, attribute: str, value: Any, operator: str = "eq") -> None:
        """Initialize an attribute specification.

        Args:
            attribute: Name of the attribute to check
            value: Value to compare against
            operator: Comparison operator (eq, ne, gt, gte, lt, lte, in, contains)
        """
        self.attribute = attribute
        self.value = value
        self.operator = operator

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the entity's attribute satisfies the condition."""
        if not hasattr(entity, self.attribute):
            return False

        entity_value = getattr(entity, self.attribute)

        if self.operator == "eq":
            return entity_value == self.value
        elif self.operator == "ne":
            return entity_value != self.value
        elif self.operator == "gt":
            return entity_value > self.value
        elif self.operator == "gte":
            return entity_value >= self.value
        elif self.operator == "lt":
            return entity_value < self.value
        elif self.operator == "lte":
            return entity_value <= self.value
        elif self.operator == "in":
            return entity_value in self.value
        elif self.operator == "contains":
            return self.value in entity_value
        else:
            return False

    def to_filter(self) -> dict[str, Any]:
        """Convert to a filter dict."""
        if self.operator == "eq":
            return {self.attribute: self.value}
        else:
            return {f"{self.attribute}__{self.operator}": self.value}


class AlwaysTrueSpecification(BaseSpecification):
    """Specification that always evaluates to true."""

    def is_satisfied_by(self, entity: Any) -> bool:  # noqa: ARG002
        """Always returns True."""
        return True

    def to_filter(self) -> dict[str, Any]:
        """Returns empty filter (matches all)."""
        return {}


class AlwaysFalseSpecification(BaseSpecification):
    """Specification that always evaluates to false."""

    def is_satisfied_by(self, entity: Any) -> bool:  # noqa: ARG002
        """Always returns False."""
        return False

    def to_filter(self) -> dict[str, Any]:
        """Returns impossible filter."""
        return {"$impossible": True}
