"""Helper methods for the Cypher query builder.

This module provides convenient helper methods that extend the query builder
with common patterns and operations.
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder


class QueryHelpers:
    """Mixin providing helper methods for common query patterns."""

    def match_memory(
        self: "CypherQueryBuilder",
        alias: str = "m",
        **properties: Any
    ) -> "CypherQueryBuilder":
        """Convenience method to match Memory nodes.
        
        Args:
            alias: Variable alias for the memory node
            **properties: Properties to match on
            
        Returns:
            Self for method chaining
        """
        return self.match(lambda p: p.node("Memory", alias, **properties))
    
    def match_conversation(
        self: "CypherQueryBuilder",
        conversation_id: UUID,
        memory_alias: str = "m",
        conv_alias: str = "c"
    ) -> "CypherQueryBuilder":
        """Match memories within a specific conversation.
        
        Args:
            conversation_id: ID of the conversation
            memory_alias: Variable alias for memory nodes
            conv_alias: Variable alias for conversation node
            
        Returns:
            Self for method chaining
        """
        return (
            self.match(lambda p: p.node("Conversation", conv_alias, id=str(conversation_id)))
                .match(lambda p: p.node(conv_alias)
                                  .rel_to("HAS_MEMORY")
                                  .node("Memory", memory_alias))
        )
    
    def match_topic_memories(
        self: "CypherQueryBuilder",
        topic_id: int,
        memory_alias: str = "m",
        topic_alias: str = "t"
    ) -> "CypherQueryBuilder":
        """Match memories belonging to a specific topic.
        
        Args:
            topic_id: ID of the topic
            memory_alias: Variable alias for memory nodes
            topic_alias: Variable alias for topic node
            
        Returns:
            Self for method chaining
        """
        return (
            self.match(lambda p: p.node("Topic", topic_alias, id=topic_id))
                .match(lambda p: p.node(topic_alias)
                                  .rel_to("CONTAINS")
                                  .node("Memory", memory_alias))
        )
    
    def with_similarity(
        self: "CypherQueryBuilder",
        node_alias: str = "m",
        embedding_param: str = "query_embedding",
        as_name: str = "similarity"
    ) -> "CypherQueryBuilder":
        """Add cosine similarity calculation.
        
        Args:
            node_alias: Alias of the node with embedding
            embedding_param: Name of the query embedding parameter
            as_name: Name for the similarity score
            
        Returns:
            Self for method chaining
        """
        similarity_calc = f"""
        {node_alias},
        reduce(dot = 0.0, i IN range(0, size({node_alias}.embedding)-1) | 
               dot + {node_alias}.embedding[i] * ${embedding_param}[i]) / 
        (sqrt(reduce(sum = 0.0, i IN range(0, size({node_alias}.embedding)-1) | 
               sum + {node_alias}.embedding[i] * {node_alias}.embedding[i])) * 
         sqrt(reduce(sum = 0.0, i IN range(0, size(${embedding_param})-1) | 
               sum + ${embedding_param}[i] * ${embedding_param}[i]))) AS {as_name}
        """
        return self.with_clause(similarity_calc)
    
    def filter_similarity(
        self: "CypherQueryBuilder",
        threshold: float = 0.7,
        similarity_alias: str = "similarity"
    ) -> "CypherQueryBuilder":
        """Filter by similarity threshold.
        
        Args:
            threshold: Minimum similarity score
            similarity_alias: Alias of the similarity score
            
        Returns:
            Self for method chaining
        """
        return self.where_param(f"{similarity_alias} > {{}}", threshold)
    
    def expand_relationships(
        self: "CypherQueryBuilder",
        from_alias: str = "m",
        to_alias: str = "related",
        relationship_types: list[str] | None = None,
        depth: int = 1,
        optional: bool = True
    ) -> "CypherQueryBuilder":
        """Expand relationships from a node.
        
        Args:
            from_alias: Alias of the source node
            to_alias: Alias for the target nodes
            relationship_types: Types of relationships to follow
            depth: Maximum depth of traversal
            optional: Whether to use OPTIONAL MATCH
            
        Returns:
            Self for method chaining
        """
        # Build relationship pattern
        rel_pattern = f":{{'|'.join(relationship_types)}}*1..{depth}" if relationship_types else f"*1..{depth}"
        
        # Use appropriate match type
        match_func = self.optional_match if optional else self.match
        
        return match_func(
            lambda p: p.node(from_alias)
                      .rel(rel_pattern)
                      .node("Memory", to_alias)
        )
    
    def collect_relationships(
        self: "CypherQueryBuilder",
        node_alias: str = "m",
        related_alias: str = "related",
        collection_name: str = "relationships"
    ) -> "CypherQueryBuilder":
        """Collect related nodes into a list.
        
        Args:
            node_alias: Alias of the main node
            related_alias: Alias of the related nodes
            collection_name: Name for the collection
            
        Returns:
            Self for method chaining
        """
        return self.with_clause(
            node_alias,
            f"COLLECT(DISTINCT {related_alias}) AS {collection_name}"
        )
    
    def paginate(
        self: "CypherQueryBuilder",
        page: int = 1,
        page_size: int = 20
    ) -> "CypherQueryBuilder":
        """Apply pagination using page number and size.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of items per page
            
        Returns:
            Self for method chaining
        """
        skip_count = (page - 1) * page_size
        if skip_count > 0:
            self.skip(skip_count)
        return self.limit(page_size)
    
    def return_memory_fields(
        self: "CypherQueryBuilder",
        alias: str = "m",
        fields: list[str] | None = None,
        include_similarity: bool = False,
        include_relationships: bool = False
    ) -> "CypherQueryBuilder":
        """Return common memory fields.
        
        Args:
            alias: Alias of the memory node
            fields: List of fields to return (default: common fields)
            include_similarity: Whether to include similarity score
            include_relationships: Whether to include relationships
            
        Returns:
            Self for method chaining
        """
        if fields is None:
            fields = ["id", "content", "memory_type", "salience", "timestamp"]
        
        return_items = [f"{alias}.{field} AS {field}" for field in fields]
        
        if include_similarity:
            return_items.append("similarity")
        
        if include_relationships:
            return_items.append("relationships")
        
        return self.return_clause(*return_items)
    
    def count(
        self: "CypherQueryBuilder",
        alias: str = "m",
        distinct: bool = True
    ) -> "CypherQueryBuilder":
        """Return count of matched nodes.
        
        Args:
            alias: Alias of the nodes to count
            distinct: Whether to count distinct nodes
            
        Returns:
            Self for method chaining
        """
        count_expr = f"COUNT(DISTINCT {alias})" if distinct else f"COUNT({alias})"
        return self.return_clause(f"{count_expr} AS count")
    
    def exists(
        self: "CypherQueryBuilder",
        alias: str = "m"
    ) -> "CypherQueryBuilder":
        """Check if any nodes match the pattern.
        
        Args:
            alias: Alias of the nodes to check
            
        Returns:
            Self for method chaining
        """
        return self.return_clause(f"EXISTS({alias}) AS exists").limit(1)
    
    def aggregate(
        self: "CypherQueryBuilder",
        alias: str = "m",
        aggregations: dict[str, str] | None = None
    ) -> "CypherQueryBuilder":
        """Apply aggregation functions.
        
        Args:
            alias: Alias of the nodes to aggregate
            aggregations: Dict of {result_name: aggregation_expression}
            
        Returns:
            Self for method chaining
        """
        if aggregations is None:
            aggregations = {
                "count": f"COUNT({alias})",
                "avg_salience": f"AVG({alias}.salience)",
                "max_salience": f"MAX({alias}.salience)",
                "min_salience": f"MIN({alias}.salience)"
            }
        
        return_items = [f"{expr} AS {name}" for name, expr in aggregations.items()]
        return self.return_clause(*return_items)
    
    def group_by(
        self: "CypherQueryBuilder",
        node_alias: str = "m",
        group_field: str = "memory_type",
        aggregations: dict[str, str] | None = None
    ) -> "CypherQueryBuilder":
        """Group results by a field with aggregations.
        
        Args:
            node_alias: Alias of the nodes to group
            group_field: Field to group by
            aggregations: Aggregation expressions
            
        Returns:
            Self for method chaining
        """
        if aggregations is None:
            aggregations = {"count": f"COUNT({node_alias})"}
        
        # Build WITH clause for grouping
        with_items = [f"{node_alias}.{group_field} AS {group_field}"]
        self.with_clause(*with_items)
        
        # Build return with aggregations
        return_items = [group_field]
        return_items.extend([f"{expr} AS {name}" for name, expr in aggregations.items()])
        
        return self.return_clause(*return_items)
    
    def filter_by_date_range(
        self: "CypherQueryBuilder",
        alias: str = "m",
        field: str = "timestamp",
        start_date: str | None = None,
        end_date: str | None = None
    ) -> "CypherQueryBuilder":
        """Filter nodes by date range.
        
        Args:
            alias: Alias of the nodes to filter
            field: Date field to filter on
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
            
        Returns:
            Self for method chaining
        """
        if start_date:
            self.where_param(f"{alias}.{field} >= datetime('{{}}')", start_date)
        if end_date:
            self.where_param(f"{alias}.{field} <= datetime('{{}}')", end_date)
        
        return self
    
    def with_access_tracking(
        self: "CypherQueryBuilder",
        alias: str = "m"
    ) -> "CypherQueryBuilder":
        """Update access tracking for matched memories.
        
        Args:
            alias: Alias of the memories to track
            
        Returns:
            Self for method chaining
        """
        return self.set_property(
            alias,
            {
                "last_accessed": "datetime()",
                "access_count": f"COALESCE({alias}.access_count, 0) + 1"
            }
        )