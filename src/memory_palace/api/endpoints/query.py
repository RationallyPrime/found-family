"""Query builder endpoint for direct Cypher query construction and execution."""

import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)
router = APIRouter()


class MatchPattern(BaseModel):
    """Pattern for MATCH clauses."""
    node_label: str = "Memory"
    node_alias: str = "m"
    properties: dict[str, Any] | None = None


class WhereCondition(BaseModel):
    """Where clause conditions."""
    condition: str
    parameters: dict[str, Any] | None = None


class QueryBuilderRequest(BaseModel):
    """Request for building and executing Cypher queries."""
    
    # Query construction
    match_patterns: list[MatchPattern] | None = None
    where_conditions: list[WhereCondition] | None = None
    with_clause: str | None = None
    return_clause: str
    order_by: str | None = None
    limit: int | None = None
    skip: int | None = None
    
    # Options
    include_similarity: bool = False
    query_embedding: list[float] | None = None
    similarity_threshold: float = 0.5
    
    # Specification filters
    min_salience: float | None = None
    topic_ids: list[int] | None = None
    conversation_id: UUID | None = None
    memory_types: list[str] | None = None
    
    # Debug mode
    return_query_only: bool = False


class QueryBuilderResponse(BaseModel):
    """Response from query builder execution."""
    query: str
    parameters: dict[str, Any]
    results: list[dict[str, Any]] | None = None
    count: int = 0
    error: str | None = None


class DirectCypherRequest(BaseModel):
    """Request for direct Cypher query execution."""
    query: str
    parameters: dict[str, Any] | None = None
    limit: int = 100


@router.post("/build", response_model=QueryBuilderResponse)
async def build_and_execute_query(
    request: QueryBuilderRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> QueryBuilderResponse:
    """Build and execute a Cypher query using the query builder."""
    try:
        logger.info("Building Cypher query", extra={
            "has_match": bool(request.match_patterns),
            "has_where": bool(request.where_conditions),
            "include_similarity": request.include_similarity
        })
        
        # Initialize query builder
        builder = CypherQueryBuilder()
        
        # Add MATCH patterns
        if request.match_patterns:
            for pattern in request.match_patterns:
                if pattern.properties:
                    builder.match(
                        lambda p: p.node(pattern.node_label, pattern.node_alias, **pattern.properties)
                    )
                else:
                    builder.match(
                        lambda p: p.node(pattern.node_label, pattern.node_alias)
                    )
        else:
            # Default match pattern
            builder.match(lambda p: p.node("Memory", "m"))
        
        # Add WHERE conditions
        if request.where_conditions:
            for condition in request.where_conditions:
                builder.where(condition.condition)
        
        # Add similarity search if requested
        if request.include_similarity and request.query_embedding:
            # Add WITH clause for similarity calculation
            similarity_calc = f"""
            m, 
            reduce(dot = 0.0, i IN range(0, size($embedding)-1) | 
                   dot + m.embedding[i] * $embedding[i]) AS dotProduct,
            sqrt(reduce(sum = 0.0, i IN range(0, size(m.embedding)-1) | 
                   sum + m.embedding[i] * m.embedding[i])) AS norm1,
            sqrt(reduce(sum = 0.0, i IN range(0, size($embedding)-1) | 
                   sum + $embedding[i] * $embedding[i])) AS norm2
            """
            builder.with_clause(similarity_calc)
            builder.with_clause("m, dotProduct / (norm1 * norm2) AS similarity")
            builder.where(f"similarity > {request.similarity_threshold}")
        
        # Add specification-based filters
        if request.min_salience is not None:
            builder.where(f"m.salience >= {request.min_salience}")
        
        if request.topic_ids:
            topic_list = ', '.join(str(t) for t in request.topic_ids)
            builder.where(f"m.topic_id IN [{topic_list}]")
        
        if request.conversation_id:
            builder.where(f"m.conversation_id = '{request.conversation_id}'")
        
        if request.memory_types:
            type_list = ', '.join(f"'{t}'" for t in request.memory_types)
            builder.where(f"m.memory_type IN [{type_list}]")
        
        # Add WITH clause if specified
        if request.with_clause:
            builder.with_clause(request.with_clause)
        
        # Add RETURN clause
        builder.return_clause(request.return_clause)
        
        # Add ORDER BY if specified
        if request.order_by:
            builder.order_by(request.order_by)
        
        # Add SKIP if specified
        if request.skip:
            builder.skip(request.skip)
        
        # Add LIMIT if specified
        if request.limit:
            builder.limit(request.limit)
        
        # Build the query
        query_str, params = builder.build()
        
        # Add embedding parameter if needed
        if request.include_similarity and request.query_embedding:
            params["embedding"] = request.query_embedding
        
        logger.info("Built Cypher query", extra={
            "query": query_str[:200],  # First 200 chars
            "param_keys": list(params.keys()) if params else []
        })
        
        # Return query only if requested
        if request.return_query_only:
            return QueryBuilderResponse(
                query=query_str,
                parameters=params,
                results=None,
                count=0
            )
        
        # Execute the query
        async with memory_service.session as session:
            result = await session.run(query_str, parameters=params)
            records = []
            async for record in result:
                # Convert record to dict
                record_dict = {}
                for key in record.keys():
                    value = record[key]
                    # Handle Neo4j nodes/relationships
                    if hasattr(value, '__dict__'):
                        record_dict[key] = dict(value)
                    else:
                        record_dict[key] = value
                records.append(record_dict)
            
            logger.info(f"Query returned {len(records)} results")
            
            return QueryBuilderResponse(
                query=query_str,
                parameters=params,
                results=records,
                count=len(records)
            )
            
    except Exception as e:
        logger.error(
            "Failed to build/execute query",
            exc_info=True,
            extra={"error": str(e)}
        )
        return QueryBuilderResponse(
            query="",
            parameters={},
            results=None,
            count=0,
            error=str(e)
        )


@router.post("/direct", response_model=QueryBuilderResponse)
async def execute_direct_cypher(
    request: DirectCypherRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> QueryBuilderResponse:
    """Execute a direct Cypher query."""
    try:
        logger.info("Executing direct Cypher query", extra={
            "query_length": len(request.query),
            "has_params": bool(request.parameters)
        })
        
        async with memory_service.session as session:
            result = await session.run(
                request.query,
                parameters=request.parameters or {}
            )
            
            records = []
            async for record in result:
                record_dict = {}
                for key in record.keys():
                    value = record[key]
                    if hasattr(value, '__dict__'):
                        record_dict[key] = dict(value)
                    else:
                        record_dict[key] = value
                records.append(record_dict)
            
            # Limit results
            if request.limit and len(records) > request.limit:
                records = records[:request.limit]
            
            logger.info(f"Direct query returned {len(records)} results")
            
            return QueryBuilderResponse(
                query=request.query,
                parameters=request.parameters or {},
                results=records,
                count=len(records)
            )
            
    except Exception as e:
        logger.error(
            "Failed to execute direct query",
            exc_info=True,
            extra={"error": str(e)}
        )
        return QueryBuilderResponse(
            query=request.query,
            parameters=request.parameters or {},
            results=None,
            count=0,
            error=str(e)
        )


@router.post("/test-similarity")
async def test_similarity_search(
    query_text: str,
    threshold: float = 0.5,
    memory_service: MemoryService = Depends(get_memory_service),
) -> dict:
    """Test similarity search with detailed debugging."""
    try:
        logger.info(f"Testing similarity search for: {query_text}")
        
        # Generate embedding
        query_embedding = await memory_service.embeddings.embed_text(query_text)
        logger.info(f"Generated embedding with dimension: {len(query_embedding)}")
        
        # First, check if we have any memories with embeddings
        check_query = """
        MATCH (m:Memory)
        WHERE m.embedding IS NOT NULL
        RETURN count(m) as count, collect(m.id)[0..3] as sample_ids
        """
        
        async with memory_service.session as session:
            result = await session.run(check_query)
            check_record = await result.single()
            
            memory_count = check_record["count"] if check_record else 0
            sample_ids = check_record["sample_ids"] if check_record else []
            
            logger.info(f"Found {memory_count} memories with embeddings")
            
            if memory_count == 0:
                return {
                    "error": "No memories with embeddings found",
                    "memory_count": 0
                }
            
            # Now try the similarity search
            similarity_query = """
            MATCH (m:Memory)
            WHERE m.embedding IS NOT NULL
            WITH m, 
                 reduce(dot = 0.0, i IN range(0, size($embedding)-1) | 
                        dot + m.embedding[i] * $embedding[i]) AS dotProduct,
                 sqrt(reduce(sum = 0.0, i IN range(0, size(m.embedding)-1) | 
                        sum + m.embedding[i] * m.embedding[i])) AS norm1,
                 sqrt(reduce(sum = 0.0, i IN range(0, size($embedding)-1) | 
                        sum + $embedding[i] * $embedding[i])) AS norm2
            WITH m, dotProduct / (norm1 * norm2) AS similarity
            WHERE similarity > $threshold
            RETURN m.id as id, m.content as content, similarity
            ORDER BY similarity DESC
            LIMIT 10
            """
            
            result = await session.run(
                similarity_query,
                parameters={
                    "embedding": query_embedding,
                    "threshold": threshold
                }
            )
            
            results = []
            async for record in result:
                results.append({
                    "id": record["id"],
                    "content": record["content"][:100] if record["content"] else None,
                    "similarity": record["similarity"]
                })
            
            logger.info(f"Similarity search returned {len(results)} results")
            
            return {
                "query": query_text,
                "threshold": threshold,
                "embedding_dimension": len(query_embedding),
                "memory_count": memory_count,
                "sample_memory_ids": sample_ids,
                "results": results,
                "result_count": len(results)
            }
            
    except Exception as e:
        logger.error(f"Test similarity search failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "query": query_text,
            "threshold": threshold
        }