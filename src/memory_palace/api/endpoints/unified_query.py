"""Unified query endpoint with declarative JSON DSL and specification support.

This module provides a single, powerful endpoint for all query needs with:
- Declarative JSON query DSL
- Automatic specification mapping
- Query builder integration
- Type-safe query construction
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.logging import get_logger
from memory_palace.domain.specifications.memory import (
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
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)
router = APIRouter()


class SpecificationFilter(BaseModel):
    """Base specification filter."""
    
    type: str = Field(..., description="Type of specification filter")


class SalienceFilter(SpecificationFilter):
    """Filter by memory salience/importance."""
    
    type: Literal["salience"] = "salience"
    min_salience: float = Field(0.5, ge=0.0, le=1.0)


class TopicFilter(SpecificationFilter):
    """Filter by topic IDs."""
    
    type: Literal["topic"] = "topic"
    topic_ids: list[int]


class ConversationFilter(SpecificationFilter):
    """Filter by conversation ID."""
    
    type: Literal["conversation"] = "conversation"
    conversation_id: UUID


class RecencyFilter(SpecificationFilter):
    """Filter by recency."""
    
    type: Literal["recency"] = "recency"
    days: int = Field(7, ge=1)
    hours: int = Field(0, ge=0)


class EmotionalFilter(SpecificationFilter):
    """Filter by emotional characteristics."""
    
    type: Literal["emotional"] = "emotional"
    min_intensity: float = Field(0.5, ge=0.0, le=1.0)
    valence_min: float = Field(-1.0, ge=-1.0, le=1.0)
    valence_max: float = Field(1.0, ge=-1.0, le=1.0)


class OntologyFilter(SpecificationFilter):
    """Filter by ontology path."""
    
    type: Literal["ontology"] = "ontology"
    path_prefix: list[str]


class ConceptFilter(SpecificationFilter):
    """Filter by concepts."""
    
    type: Literal["concepts"] = "concepts"
    concepts: list[str]


class FrequencyFilter(SpecificationFilter):
    """Filter by access frequency."""
    
    type: Literal["frequency"] = "frequency"
    min_access_count: int = Field(5, ge=1)


class DecayFilter(SpecificationFilter):
    """Filter for decaying memories."""
    
    type: Literal["decay"] = "decay"
    days_since_access: int = Field(30, ge=1)
    max_salience: float = Field(0.3, ge=0.0, le=1.0)


class RelationshipFilter(SpecificationFilter):
    """Filter by relationships to other memories."""
    
    type: Literal["related"] = "related"
    source_id: UUID
    relationship_types: list[str] | None = None
    min_strength: float = Field(0.0, ge=0.0, le=1.0)


class CompositeFilter(BaseModel):
    """Composite filter for combining specifications."""
    
    type: Literal["composite"] = "composite"
    operator: Literal["and", "or"]
    filters: list[Any]  # Use Any to break circular reference, validate at runtime


# Define the discriminated union using Annotated pattern
FilterType = Annotated[
    SalienceFilter | TopicFilter | ConversationFilter | RecencyFilter | EmotionalFilter | OntologyFilter | ConceptFilter | FrequencyFilter | DecayFilter | RelationshipFilter | CompositeFilter,
    Field(discriminator="type")
]


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
    filters: FilterType | None = Field(None, description="Specification-based filters")
    
    # Similarity search
    similarity: SimilaritySearch | None = Field(None, description="Similarity search configuration")
    
    # Relationship traversal
    expand_relationships: bool = Field(False, description="Whether to expand relationships")
    relationship_depth: int = Field(1, ge=1, le=3, description="Depth of relationship traversal")
    relationship_types: list[str] | None = Field(None, description="Filter relationship types")
    
    # Return configuration
    return_fields: list[str] = Field(
        default=["id", "content", "memory_type", "salience", "timestamp"],
        description="Fields to return"
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


def build_specification(filter_spec: FilterType | dict[str, Any]):
    """Convert filter DSL to specification objects."""
    
    # Handle dict representation (for nested filters in CompositeFilter)
    if isinstance(filter_spec, dict):
        filter_type = filter_spec.get("type")
        # Remove 'type' from the dict before passing to constructors
        filter_data = {k: v for k, v in filter_spec.items() if k != "type"}
        
        if filter_type == "salience":
            filter_spec = SalienceFilter(**filter_data)
        elif filter_type == "topic":
            filter_spec = TopicFilter(**filter_data)
        elif filter_type == "conversation":
            filter_spec = ConversationFilter(**filter_data)
        elif filter_type == "recency":
            filter_spec = RecencyFilter(**filter_data)
        elif filter_type == "emotional":
            filter_spec = EmotionalFilter(**filter_data)
        elif filter_type == "ontology":
            filter_spec = OntologyFilter(**filter_data)
        elif filter_type == "concepts":
            filter_spec = ConceptFilter(**filter_data)
        elif filter_type == "frequency":
            filter_spec = FrequencyFilter(**filter_data)
        elif filter_type == "decay":
            filter_spec = DecayFilter(**filter_data)
        elif filter_type == "related":
            filter_spec = RelationshipFilter(**filter_data)
        elif filter_type == "composite":
            filter_spec = CompositeFilter(**filter_data)
        else:
            raise ValueError(f"Unknown filter type: {filter_type}")
    
    if isinstance(filter_spec, CompositeFilter):
        # Recursively build composite specifications
        # Each filter in filters is either a dict or a FilterType
        sub_specs = [build_specification(f) for f in filter_spec.filters]
        
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
    
    # Map filter types to specification classes
    if isinstance(filter_spec, SalienceFilter):
        return SalientMemorySpecification(filter_spec.min_salience)
    
    elif isinstance(filter_spec, TopicFilter):
        return TopicMemorySpecification(filter_spec.topic_ids)
    
    elif isinstance(filter_spec, ConversationFilter):
        return ConversationMemorySpecification(filter_spec.conversation_id)
    
    elif isinstance(filter_spec, RecencyFilter):
        return RecentMemorySpecification(filter_spec.days, filter_spec.hours)
    
    elif isinstance(filter_spec, EmotionalFilter):
        return EmotionalMemorySpecification(
            filter_spec.min_intensity,
            (filter_spec.valence_min, filter_spec.valence_max)
        )
    
    elif isinstance(filter_spec, OntologyFilter):
        return OntologyPathSpecification(filter_spec.path_prefix)
    
    elif isinstance(filter_spec, ConceptFilter):
        return ConceptMemorySpecification(filter_spec.concepts)
    
    elif isinstance(filter_spec, FrequencyFilter):
        return FrequentlyAccessedSpecification(filter_spec.min_access_count)
    
    elif isinstance(filter_spec, DecayFilter):
        return DecayingMemorySpecification(
            filter_spec.days_since_access,
            filter_spec.max_salience
        )
    
    elif isinstance(filter_spec, RelationshipFilter):
        # Convert string relationship types to RelationType enum if needed
        from memory_palace.domain.models.base import RelationType
        rel_types = None
        if filter_spec.relationship_types:
            rel_types = [RelationType(rt) if isinstance(rt, str) else rt 
                        for rt in filter_spec.relationship_types]
        return RelatedMemorySpecification(
            filter_spec.source_id,
            rel_types,
            filter_spec.min_strength
        )
    
    else:
        raise ValueError(f"Unknown filter type: {type(filter_spec)}")


@router.post("/query", response_model=UnifiedQueryResponse)
async def execute_unified_query(
    request: UnifiedQueryRequest,
    memory_service: MemoryService = Depends(get_memory_service),
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
        logger.info("Executing unified query", extra={
            "has_filters": bool(dsl.filters),
            "has_similarity": bool(dsl.similarity),
            "expand_relationships": dsl.expand_relationships,
            "limit": dsl.limit
        })
        
        # Initialize query builder
        builder = CypherQueryBuilder()
        params = {}
        
        # Start with basic node match
        builder.match(lambda p: p.node(dsl.node_label, dsl.node_alias))
        
        # Apply specification-based filters
        specification_tree = None
        if dsl.filters:
            spec = build_specification(dsl.filters)
            builder.where_spec(spec)
            
            # Build specification tree for debug info
            if request.debug:
                specification_tree = {
                    "type": type(spec).__name__,
                    "cypher": spec.to_cypher() if hasattr(spec, 'to_cypher') else None
                }
        
        # Apply similarity search
        if dsl.similarity:
            # Get embedding for the query
            query_embedding = await memory_service.embeddings.embed_text(dsl.similarity.query)
            params["query_embedding"] = query_embedding
            params["similarity_threshold"] = dsl.similarity.threshold
            
            # Add similarity calculation
            builder.with_clause(
                f"{dsl.node_alias}",
                f"""
                reduce(dot = 0.0, i IN range(0, size({dsl.node_alias}.embedding)-1) | 
                       dot + {dsl.node_alias}.embedding[i] * $query_embedding[i]) / 
                (sqrt(reduce(sum = 0.0, i IN range(0, size({dsl.node_alias}.embedding)-1) | 
                       sum + {dsl.node_alias}.embedding[i] * {dsl.node_alias}.embedding[i])) * 
                 sqrt(reduce(sum = 0.0, i IN range(0, size($query_embedding)-1) | 
                       sum + $query_embedding[i] * $query_embedding[i]))) AS similarity
                """
            )
            builder.where("similarity > $similarity_threshold")
        
        # Add relationship expansion if requested
        if dsl.expand_relationships:
            rel_pattern = f"*1..{dsl.relationship_depth}"
            if dsl.relationship_types:
                rel_types = "|".join(dsl.relationship_types)
                rel_pattern = f":{rel_types}{rel_pattern}"
            
            builder.optional_match(
                lambda p: p.node(dsl.node_alias)
                          .rel(rel_pattern)
                          .node(dsl.node_label, "related")
            )
            
            if dsl.include_relationships:
                with_items = [
                    f"{dsl.node_alias}",
                    "COLLECT(DISTINCT related) AS relationships"
                ]
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
            total_available=None  # Could run COUNT query if needed
        )
        
        # Add debug information if requested
        if request.debug:
            response.query_cypher = cypher_query
            response.query_parameters = debug_params
            response.execution_time_ms = execution_time
            response.specification_tree = specification_tree
        
        return response
        
    except Exception as e:
        logger.error(
            "Failed to execute unified query",
            exc_info=True,
            extra={"error": str(e)}
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


