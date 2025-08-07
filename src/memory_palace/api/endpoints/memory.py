"""Memory API endpoints."""

import traceback
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.config import settings
from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)
router = APIRouter()


class StoreTurnRequest(BaseModel):
    """Request model for storing a conversation turn."""

    user_content: str
    assistant_content: str
    conversation_id: UUID | None = None
    metadata: dict | None = None

    # Incremental ontology support
    ontology_path: list[str] | None = None
    salience: float | None = None


class StoreTurnResponse(BaseModel):
    """Response model for storing a turn."""

    turn_id: UUID
    message: str = "Turn stored successfully"


class SearchRequest(BaseModel):
    """Request model for searching memories."""

    query: str
    k: int = 10
    threshold: float = 0.7

    # Enhanced search filters
    min_salience: float | None = None
    topic_ids: list[int] | None = None
    ontology_path: list[str] | None = None


class SearchResponse(BaseModel):
    """Response model for search results."""

    messages: list[dict]
    count: int


@router.post("/remember", response_model=StoreTurnResponse)
async def remember_turn(
    request: StoreTurnRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> StoreTurnResponse:
    """Store a conversation turn in memory."""
    try:
        logger.info("Storing conversation turn", extra={
            "user_content_length": len(request.user_content),
            "assistant_content_length": len(request.assistant_content),
            "conversation_id": str(request.conversation_id) if request.conversation_id else None
        })

        user_memory, assistant_memory = await memory_service.remember_turn(
            user_content=request.user_content,
            assistant_content=request.assistant_content,
            conversation_id=request.conversation_id,
            # remember_turn doesn't take metadata, ontology_path, or salience directly
        )

        # Use the assistant memory ID as the turn ID since that's the "response" part
        logger.info("Successfully stored turn", extra={"turn_id": str(assistant_memory.id)})
        return StoreTurnResponse(turn_id=assistant_memory.id)
    except Exception as e:
        logger.error(
            "Failed to store conversation turn",
            exc_info=True,
            extra={
                "error": str(e),
                "traceback": traceback.format_exc()
            }
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/recall", response_model=SearchResponse)
async def recall_memories(
    request: SearchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> SearchResponse:
    """Search and recall relevant memories."""
    try:
        logger.info("Searching memories", extra={
            "query": request.query,
            "k": request.k,
            "threshold": request.threshold
        })

        messages = await memory_service.search_memories(
            query=request.query,
            limit=request.k,  # Map k to limit
            similarity_threshold=request.threshold,  # Pass threshold to service
            min_salience=request.min_salience,
            topic_id=request.topic_ids[0] if request.topic_ids else None,
        )

        # Convert to dict for response
        message_dicts = []
        for msg in messages:
            msg_dict = {
                "id": str(msg.id),
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "memory_type": msg.memory_type.value,
            }
            # Add role based on memory type with personalized names
            if msg.memory_type.value == "friend_utterance":
                msg_dict["role"] = settings.friend_name
            elif msg.memory_type.value == "claude_utterance":
                msg_dict["role"] = settings.claude_name
            elif msg.memory_type.value == "user_utterance":  # Legacy support
                msg_dict["role"] = settings.friend_name
            elif msg.memory_type.value == "assistant_utterance":  # Legacy support
                msg_dict["role"] = settings.claude_name
            else:
                msg_dict["role"] = msg.memory_type.value
            
            message_dicts.append(msg_dict)

        logger.info("Search completed", extra={
            "result_count": len(messages)
        })

        return SearchResponse(
            messages=message_dicts,
            count=len(messages),
        )
    except Exception as e:
        logger.error(
            "Failed to search memories",
            exc_info=True,
            extra={
                "error": str(e),
                "query": request.query,
                "traceback": traceback.format_exc()
            }
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


class QueryBuilderRequest(BaseModel):
    """Request model for query builder endpoint."""
    
    # Match patterns
    node_label: str = "Memory"
    node_filters: dict[str, Any] | None = None
    
    # Optional similarity search
    use_similarity: bool = False
    query_text: str | None = None
    similarity_threshold: float = 0.5
    
    # Return options
    return_fields: list[str] = ["id", "content", "memory_type"]
    order_by: str | None = None
    limit: int = 10
    
    # Advanced options
    include_relationships: bool = False
    relationship_depth: int = 1


class QueryBuilderResponse(BaseModel):
    """Response model for query builder results."""
    
    cypher_query: str
    parameters: dict[str, Any]
    results: list[dict[str, Any]]
    count: int


@router.post("/query", response_model=QueryBuilderResponse)
async def execute_query_builder(
    request: QueryBuilderRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> QueryBuilderResponse:
    """Execute a Cypher query using the query builder pattern."""
    try:
        logger.info("Building Cypher query", extra={
            "node_label": request.node_label,
            "use_similarity": request.use_similarity,
            "limit": request.limit
        })
        
        # Build the query using CypherQueryBuilder
        builder = CypherQueryBuilder()
        
        # Start with basic match
        if request.node_filters:
            builder.match(lambda p: p.node(request.node_label, "n", **request.node_filters))
        else:
            builder.match(lambda p: p.node(request.node_label, "n"))
        
        # Add similarity search if requested
        params = {}
        if request.use_similarity and request.query_text:
            # Get embedding for the query
            query_embedding = await memory_service.embeddings.embed_text(request.query_text)
            params["query_embedding"] = query_embedding
            
            # Add cosine similarity calculation using parameter
            builder.with_clause(
                "n",
                """
                reduce(dot = 0.0, i IN range(0, size(n.embedding)-1) | 
                       dot + n.embedding[i] * $query_embedding[i]) / 
                (sqrt(reduce(sum = 0.0, i IN range(0, size(n.embedding)-1) | 
                       sum + n.embedding[i] * n.embedding[i])) * 
                 sqrt(reduce(sum = 0.0, i IN range(0, size($query_embedding)-1) | 
                       sum + $query_embedding[i] * $query_embedding[i]))) AS similarity
                """
            )
            builder.where_param("similarity > {}", request.similarity_threshold)
        
        # Add relationship traversal if requested
        if request.include_relationships:
            builder.optional_match(
                lambda p: p.node("n")
                          .rel(f"*1..{request.relationship_depth}")
                          .node(request.node_label, "related")
            )
            builder.with_clause("n", "COLLECT(DISTINCT related) AS relationships")
        
        # Build return clause
        return_items = []
        for field in request.return_fields:
            return_items.append(f"n.{field} AS {field}")
        
        if request.use_similarity:
            return_items.append("similarity")
        
        if request.include_relationships:
            return_items.append("relationships")
        
        builder.return_clause(*return_items)
        
        # Add ordering if specified
        if request.order_by:
            builder.order_by(request.order_by)
        elif request.use_similarity:
            builder.order_by("similarity DESC")
        
        # Add limit
        builder.limit(request.limit)
        
        # Get the query and parameters
        cypher_query, builder_params = builder.build()
        
        # Merge parameters
        all_params = {**builder_params, **params}
        
        logger.info(f"Executing Cypher query: {cypher_query}")
        logger.debug(f"Parameters: {list(all_params.keys())}")
        
        # Execute using the session from memory_service
        result = await memory_service.run_query(cypher_query, **all_params)
        records = await result.data()
        
        logger.info(f"Query returned {len(records)} results")
        
        # Remove embedding from parameters for response (too large)
        response_params = {k: v for k, v in all_params.items() 
                          if k != "query_embedding"}
        if "query_embedding" in all_params:
            response_params["query_embedding"] = f"<embedding vector of length {len(all_params['query_embedding'])}>"
        
        return QueryBuilderResponse(
            cypher_query=cypher_query,
            parameters=response_params,
            results=records,
            count=len(records)
        )
        
    except Exception as e:
        logger.error(
            "Failed to execute query builder",
            exc_info=True,
            extra={
                "error": str(e),
                "traceback": traceback.format_exc()
            }
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/raw_query")
async def execute_raw_query(
    query: str,
    parameters: dict[str, Any] | None = None,
    memory_service: MemoryService = Depends(get_memory_service),
) -> dict:
    """Execute a raw Cypher query (for debugging)."""
    try:
        logger.warning(f"Executing raw Cypher query: {query}")
        
        result = await memory_service.run_query(query, **(parameters or {}))
        records = await result.data()
        
        return {
            "query": query,
            "parameters": parameters,
            "results": records,
            "count": len(records)
        }
        
    except Exception as e:
        logger.error(f"Raw query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/health")
async def health_check() -> dict:
    """Check if memory service is healthy."""
    return {"status": "healthy", "service": "memory"}
