"""Composite specification implementations.

This module provides base classes for building composite specifications
that can be extended for specific domain needs.
"""

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class BaseSpecification(BaseModel):
    """Base class for concrete specifications.

    This provides default implementations of the composition methods
    so that concrete specifications only need to implement the core logic.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the entity satisfies this specification."""
        raise NotImplementedError("Subclasses must implement is_satisfied_by")

    def to_filter(self) -> dict[str, Any]:
        """Convert this specification to a filter dict."""
        return {}

    def and_(self, other: "BaseSpecification") -> "BaseSpecification":
        """Combine with another specification using AND logic."""
        return AndSpecification(left=self, right=other)

    def or_(self, other: "BaseSpecification") -> "BaseSpecification":
        """Combine with another specification using OR logic."""
        return OrSpecification(left=self, right=other)

    def not_(self) -> "BaseSpecification":
        """Negate this specification."""
        return NotSpecification(spec=self)


class AndSpecification(BaseSpecification):
    """AND specification implementation."""

    type: Literal["and"] = "and"
    left: BaseSpecification
    right: BaseSpecification

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

    def to_cypher(self) -> str:
        """Combine Cypher expressions with AND logic."""
        left_cypher = self.left.to_cypher() if hasattr(self.left, "to_cypher") else None
        right_cypher = self.right.to_cypher() if hasattr(self.right, "to_cypher") else None

        if left_cypher and right_cypher:
            return f"{left_cypher} AND {right_cypher}"
        elif left_cypher:
            return left_cypher
        elif right_cypher:
            return right_cypher
        else:
            return "true"


class OrSpecification(BaseSpecification):
    """OR specification implementation."""

    type: Literal["or"] = "or"
    left: BaseSpecification
    right: BaseSpecification

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if either specification is satisfied."""
        return self.left.is_satisfied_by(entity) or self.right.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Combine filters with OR logic."""
        left_filter = self.left.to_filter()
        right_filter = self.right.to_filter()
        return {"$or": [left_filter, right_filter]}

    def to_cypher(self) -> str:
        """Combine Cypher expressions with OR logic."""
        left_cypher = self.left.to_cypher() if hasattr(self.left, "to_cypher") else None
        right_cypher = self.right.to_cypher() if hasattr(self.right, "to_cypher") else None

        if left_cypher and right_cypher:
            return f"({left_cypher}) OR ({right_cypher})"
        elif left_cypher:
            return left_cypher
        elif right_cypher:
            return right_cypher
        else:
            return "true"


class NotSpecification(BaseSpecification):
    """NOT specification implementation."""

    type: Literal["not"] = "not"
    spec: BaseSpecification

    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if the specification is NOT satisfied."""
        return not self.spec.is_satisfied_by(entity)

    def to_filter(self) -> dict[str, Any]:
        """Create a negated filter."""
        return {"$not": self.spec.to_filter()}


class AttributeSpecification(BaseSpecification):
    """Specification that checks an attribute value."""

    attribute: str
    value: Any
    operator: str = "eq"  # Comparison operator (eq, ne, gt, gte, lt, lte, in, contains)

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


class CompositeSpecification(BaseSpecification):
    """Composite specification for combining multiple specifications."""

    type: Literal["composite"] = "composite"
    operator: Literal["and", "or"] = Field(...)
    specifications: list[BaseSpecification] = Field(...)

    def is_satisfied_by(self, entity: Any) -> bool:
        if self.operator == "and":
            return all(spec.is_satisfied_by(entity) for spec in self.specifications)
        else:  # "or"
            return any(spec.is_satisfied_by(entity) for spec in self.specifications)

    def to_cypher(self) -> str:
        cypher_clauses = [spec.to_cypher() for spec in self.specifications if hasattr(spec, "to_cypher")]
        if not cypher_clauses:
            return "true"

        if self.operator == "and":
            return " AND ".join(f"({clause})" for clause in cypher_clauses)
        else:  # "or"
            return " OR ".join(f"({clause})" for clause in cypher_clauses)

    def to_filter(self) -> dict[str, Any]:
        filters = [spec.to_filter() for spec in self.specifications]
        if self.operator == "and":
            # Merge all filters
            result = {}
            for f in filters:
                result.update(f)
            return result
        else:  # "or"
            return {"$or": filters}
