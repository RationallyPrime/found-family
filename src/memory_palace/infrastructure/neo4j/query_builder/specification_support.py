from typing import TYPE_CHECKING
from memory_palace.domain.specifications.base import Specification

if TYPE_CHECKING:
    from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder


class SpecificationSupport:
    """Mixin to integrate specifications with query builder."""
    
    def where_spec(self: "CypherQueryBuilder", spec: Specification) -> "CypherQueryBuilder":
        """Apply a specification as a WHERE clause."""
        # Check if spec has Cypher support
        if hasattr(spec, 'to_cypher'):
            cypher = spec.to_cypher()
            return self.where(cypher)
        
        # Fallback to filter dict conversion
        filters = spec.to_filter()
        return self._apply_filters(filters)
    
    def _apply_filters(self: "CypherQueryBuilder", filters: dict) -> "CypherQueryBuilder":
        """Convert filter dict to WHERE clauses."""
        conditions = []
        
        for key, value in filters.items():
            if "__" in key:
                # Handle operators like 'salience__gte'
                field, op = key.rsplit("__", 1)
                if op == "gte":
                    conditions.append(f"m.{field} >= {value}")
                elif op == "lte":
                    conditions.append(f"m.{field} <= {value}")
                elif op == "in":
                    if isinstance(value, (list, tuple)):
                        value_str = str(list(value))
                    else:
                        value_str = f"[{value}]"
                    conditions.append(f"m.{field} IN {value_str}")
                elif op == "ne":
                    conditions.append(f"m.{field} <> {value}")
                elif op == "gt":
                    conditions.append(f"m.{field} > {value}")
                elif op == "lt":
                    conditions.append(f"m.{field} < {value}")
                elif op == "contains":
                    conditions.append(f"m.{field} CONTAINS {repr(str(value))}")
                # Add more operators as needed
            elif key == "$or":
                # Handle OR conditions
                or_parts = []
                for or_filter in value:
                    or_conditions = []
                    for or_key, or_value in or_filter.items():
                        if "__" in or_key:
                            field, op = or_key.rsplit("__", 1)
                            if op == "gte":
                                or_conditions.append(f"m.{field} >= {or_value}")
                            elif op == "lte":
                                or_conditions.append(f"m.{field} <= {or_value}")
                            # Add other operators as needed
                        else:
                            or_conditions.append(f"m.{or_key} = {repr(or_value) if isinstance(or_value, str) else or_value}")
                    if or_conditions:
                        or_parts.append(" AND ".join(or_conditions))
                
                if or_parts:
                    conditions.append(f"({' OR '.join(or_parts)})")
            else:
                # Simple equality - handle string values with proper quoting
                if isinstance(value, str):
                    conditions.append(f"m.{key} = {repr(value)}")
                else:
                    conditions.append(f"m.{key} = {value}")
        
        if conditions:
            return self.where(" AND ".join(conditions))
        return self
    
    def _filters_to_cypher(self: "CypherQueryBuilder", filters: dict) -> str:
        """Helper to convert a single filter dict to Cypher condition."""
        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                conditions.append(f"m.{key} = {repr(value)}")
            else:
                conditions.append(f"m.{key} = {value}")
        return " AND ".join(conditions) if conditions else "true"
