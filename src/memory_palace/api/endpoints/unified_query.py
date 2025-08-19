"""Unified query endpoint with declarative JSON DSL and specification support.

This module provides a single, powerful endpoint for all query needs with:
- Declarative JSON query DSL
- Automatic specification mapping
- Query builder integration
- Type-safe query construction
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.logging import get_logger
from memory_palace.domain.specifications.memory import (
    MemorySpecification,  # Use the discriminated union directly
    CompositeSpecification,
    ConceptMemorySpecification,
    ConversationMemorySpecification,
    DecayingMemorySpecification,
    EmotionalMemorySpecification,
    FrequentlyAccessedSpecification,
    OntologyPathSpecification,
    RecentMemorySpecification,
    RelatedMemorySpecification,
    SalientMemorySpecification,
    TopicMemorySpecification,
)
from memory_palace.domain.specifications.similarity import SimilaritySpecification
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)
router = APIRouter()


class SimilaritySearch(BaseModel):
    """Configuration for similarity search."""

    query: str = Field(..., description="Text query for similarity search")
    threshold: float = Field(0.7, ge=0.0, le=1.0, description="Similarity threshold")
    boost_factor: float = Field(1.0, ge=0.0, description="Boost factor for similarity scores")


class QueryDSL(BaseModel):
    """Declarative query DSL for the unified endpoint."""

    # Node selection
    node_label: str = Field("Memory", description="Neo4j node label to query")
    node_alias: str = Field("m", description="Variable alias for the node")

    # Filtering via specifications
    filters: MemorySpecification | None = Field(None, description="Specification-based filters")

    # Similarity search
    similarity: SimilaritySearch | None = Field(None, description="Similarity search configuration")

    # Relationship traversal
    expand_relationships: bool = Field(False, description="Whether to expand relationships")
    relationship_depth: int = Field(1, ge=1, le=3, description="Depth of relationship traversal")
    relationship_types: list[str] | None = Field(None, description="Filter relationship types")

    # Return configuration
    return_fields: list[str] = Field(
        default=["id", "content", "memory_type", "salience", "timestamp"], description="Fields to return"
    )
    include_similarity_score: bool = Field(True, description="Include similarity score if using similarity search")
    include_relationships: bool = Field(False, description="Include related nodes in results")

    # Ordering and pagination
    order_by: str | None = Field(None, description="Order by clause (e.g., 'm.timestamp DESC')")
    order_by_similarity: bool = Field(True, description="Order by similarity if using similarity search")
    limit: int = Field(20, ge=1, le=100, description="Maximum results to return")
    skip: int = Field(0, ge=0, description="Number of results to skip")

    # Advanced options
    distinct: bool = Field(True, description="Return distinct results")
    timeout_ms: int = Field(30000, ge=1000, le=60000, description="Query timeout in milliseconds")
    explain: bool = Field(False, description="Return query execution plan")


class UnifiedQueryRequest(BaseModel):
    """Request for the unified query endpoint."""

    query: QueryDSL = Field(..., description="Query DSL specification")
    debug: bool = Field(False, description="Enable debug mode to return query details")


class UnifiedQueryResponse(BaseModel):
    """Response from the unified query endpoint."""

    results: list[dict[str, Any]]
    count: int
    total_available: int | None = None

    # Debug information
    query_cypher: str | None = None
    query_parameters: dict[str, Any] | None = None
    execution_time_ms: float | None = None
    specification_tree: dict | None = None


def parse_order_by(order_by_str: str) -> tuple[str | None, str, str] | None:
    """Parse an order_by string like 'm.timestamp DESC' into components.
    
    Returns:
        Tuple of (node_alias, field_name, direction) or None if parse fails
        Example: 'm.timestamp DESC' -> ('m', 'timestamp', 'DESC')
        Example: 'timestamp DESC' -> (None, 'timestamp', 'DESC')
    """
    if not order_by_str:
        return None
    
    parts = order_by_str.strip().split()
    if not parts:
        return None
    
    # Handle both "m.field DESC" and "field DESC" formats
    field_part = parts[0]
    direction = parts[1].upper() if len(parts) > 1 else "ASC"
    
    if "." in field_part:
        # Format: m.field
        node_parts = field_part.split(".", 1)
        if len(node_parts) == 2:
            return (node_parts[0], node_parts[1], direction)
    else:
        # Format: field (no node alias)
        return (None, field_part, direction)
    
    return None


def build_specification(filter_spec: MemorySpecification):
    """Build composite specifications if needed.
    
    Since specifications are already Pydantic models, we just need to handle
    the CompositeSpecification case where we need to combine multiple specs.
    """
    if isinstance(filter_spec, CompositeSpecification):
        # Recursively build composite specifications
        sub_specs = [build_specification(spec) for spec in filter_spec.specifications]
        
        if filter_spec.operator == "and":
            spec = sub_specs[0]
            for s in sub_specs[1:]:
                spec = spec.and_(s)  # Use the and_ method
            return spec
        else:  # "or"
            spec = sub_specs[0]
            for s in sub_specs[1:]:
                spec = spec.or_(s)  # Use the or_ method
            return spec
    
    # For all other specifications, they're already ready to use
    return filter_spec


@router.post("/query", response_model=UnifiedQueryResponse, operation_id="query")
async def execute_unified_query(
    request: UnifiedQueryRequest,
    memory_service: MemoryService = Depends(get_memory_service),  # noqa: B008
) -> UnifiedQueryResponse:
    """Execute a unified query using the declarative DSL.

    This endpoint consolidates all query functionality into a single,
    powerful interface that supports:

    - Specification-based filtering with composable logic
    - Similarity search with configurable thresholds
    - Relationship traversal and graph exploration
    - Flexible return field selection
    - Advanced ordering and pagination

    Example DSL query:
    ```json
    {
        "query": {
            "filters": {
                "operator": "and",
                "filters": [
                    {"type": "salience", "min_salience": 0.7},
                    {"type": "recency", "days": 7}
                ]
            },
            "similarity": {
                "query": "machine learning concepts",
                "threshold": 0.8
            },
            "expand_relationships": true,
            "relationship_depth": 2,
            "return_fields": ["id", "content", "salience"],
            "limit": 10
        }
    }
    ```
    """
    try:
        import time

        start_time = time.time()

        dsl = request.query
        logger.info(
            "Executing unified query",
            extra={
                "has_filters": bool(dsl.filters),
                "has_similarity": bool(dsl.similarity),
                "expand_relationships": dsl.expand_relationships,
                "limit": dsl.limit,
            },
        )

        # Initialize query builder
        builder = CypherQueryBuilder()
        params = {}

        # Start with basic node match
        builder.match(lambda p: p.node(dsl.node_label, dsl.node_alias))

        # Build combined specification including similarity if needed
        specification_tree = None
        specs_to_combine = []

        # Add filter specifications
        if dsl.filters:
            filter_spec = build_specification(dsl.filters)
            specs_to_combine.append(filter_spec)

        # Add similarity specification if requested
        similarity_spec = None
        if dsl.similarity:
            # Get embedding for the query
            query_embedding = await memory_service.embeddings.embed_text(dsl.similarity.query)

            similarity_spec = SimilaritySpecification(
                embedding=query_embedding, threshold=dsl.similarity.threshold, alias=dsl.node_alias
            )
            # We'll handle similarity separately due to its special requirements
            params["query_embedding"] = query_embedding
            params["similarity_threshold"] = dsl.similarity.threshold

        # Apply non-similarity filters first
        if specs_to_combine:
            combined_spec = specs_to_combine[0]
            for spec in specs_to_combine[1:]:
                combined_spec = combined_spec.and_(spec)

            builder.where_spec(combined_spec)

            if request.debug:
                specification_tree = {
                    "type": type(combined_spec).__name__,
                    "cypher": combined_spec.to_cypher() if hasattr(combined_spec, "to_cypher") else None,
                }

        # If we have similarity, add WITH clause for calculation then filter
        if similarity_spec:
            # Calculate similarity in WITH clause
            builder.with_clause(
                f"{dsl.node_alias}",
                f"{similarity_spec.get_similarity_calculation()} AS similarity",
            )

            # Filter by similarity threshold
            builder.where("similarity > $similarity_threshold")

        # Add relationship expansion if requested
        if dsl.expand_relationships:
            rel_pattern = f"*1..{dsl.relationship_depth}"
            if dsl.relationship_types:
                rel_types = "|".join(dsl.relationship_types)
                rel_pattern = f":{rel_types}{rel_pattern}"

            builder.optional_match(
                lambda p: p.node(variable=dsl.node_alias).rel(rel_pattern).node(dsl.node_label, "related")
            )

            if dsl.include_relationships:
                with_items = [f"{dsl.node_alias}", "COLLECT(DISTINCT related) AS relationships"]
                if dsl.similarity:
                    with_items.append("similarity")
                builder.with_clause(*with_items)

        # Build return clause
        return_items = []
        for field in dsl.return_fields:
            return_items.append(f"{dsl.node_alias}.{field} AS {field}")

        if dsl.similarity and dsl.include_similarity_score:
            return_items.append("similarity")

        if dsl.expand_relationships and dsl.include_relationships:
            return_items.append("relationships")

        # Apply distinct if requested (only on the first item, Neo4j syntax)
        if dsl.distinct and return_items:
            return_items[0] = f"DISTINCT {return_items[0]}"

        builder.return_clause(*return_items)

        # Add ordering
        if dsl.similarity and dsl.order_by_similarity:
            builder.order_by("similarity DESC")
        elif dsl.order_by:
            # Handle DISTINCT + ORDER BY compatibility
            if dsl.distinct:
                # Parse the order_by to check if we need to transform it
                parsed = parse_order_by(dsl.order_by)
                if parsed:
                    node_alias, field_name, direction = parsed
                    # Check if field is in return_fields
                    if field_name in dsl.return_fields:
                        # Use just the field alias, not node.field
                        transformed_order = f"{field_name} {direction}"
                        builder.order_by(transformed_order)
                        logger.debug(
                            f"Transformed ORDER BY for DISTINCT: {dsl.order_by} -> {transformed_order}"
                        )
                    else:
                        # Field not in RETURN clause, can't order by it with DISTINCT
                        logger.warning(
                            f"Cannot ORDER BY {field_name} with DISTINCT - field not in return_fields. Skipping ordering.",
                            extra={"field": field_name, "return_fields": dsl.return_fields}
                        )
                else:
                    # Couldn't parse, use as-is and hope for the best
                    logger.warning(f"Could not parse ORDER BY clause: {dsl.order_by}")
                    builder.order_by(dsl.order_by)
            else:
                # No DISTINCT, use order_by as-is
                builder.order_by(dsl.order_by)

        # Add pagination
        if dsl.skip > 0:
            builder.skip(dsl.skip)
        builder.limit(dsl.limit)

        # Build and execute query
        cypher_query, builder_params = builder.build()
        all_params = {**builder_params, **params}

        # Remove embedding from debug params (too large)
        debug_params = None
        if request.debug:
            debug_params = {k: v for k, v in all_params.items() if k != "query_embedding"}
            if "query_embedding" in all_params:
                debug_params["query_embedding"] = f"<vector[{len(all_params['query_embedding'])}]>"

        logger.info(f"Executing Cypher query: {cypher_query[:200]}...")

        # Execute query
        result = await memory_service.run_query(cypher_query, **all_params)
        records = await result.data()

        # Calculate execution time
        execution_time = (time.time() - start_time) * 1000

        logger.info(f"Query completed in {execution_time:.2f}ms with {len(records)} results")

        # Build response
        response = UnifiedQueryResponse(
            results=records,
            count=len(records),
            total_available=None,  # Could run COUNT query if needed
        )

        # Add debug information if requested
        if request.debug:
            response.query_cypher = cypher_query
            response.query_parameters = debug_params
            response.execution_time_ms = execution_time
            response.specification_tree = specification_tree

        return response

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        from memory_palace.core.base import ServiceErrorDetails
        from memory_palace.core.errors import ProcessingError
        
        logger.error("Failed to execute unified query", exc_info=True)
        
        # Convert to ProcessingError for proper error handling
        error = ProcessingError(
            message=f"Failed to execute unified query: {e}",
            details=ServiceErrorDetails(
                source="unified_query",
                operation="execute_unified_query",
                service_name="memory_palace",
                endpoint="/api/v1/unified_query",
                status_code=500
            )
        )
        raise HTTPException(status_code=500, detail=str(error)) from e
