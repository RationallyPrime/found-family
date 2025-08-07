"""Core API endpoints for Memory Palace."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)

router = APIRouter()


class ConversationTurnRequest(BaseModel):
    user_content: str
    assistant_content: str
    conversation_id: UUID | None = None
    detect_relationships: bool = True
    auto_classify: bool = True


class ConversationTurnResponse(BaseModel):
    user_memory_id: UUID
    assistant_memory_id: UUID
    conversation_id: UUID | None
    relationships_created: int


class SearchRequest(BaseModel):
    query: str | None = None
    conversation_id: UUID | None = None
    topic_id: int | None = None
    min_salience: float | None = None
    limit: int = 50


class MemoryResponse(BaseModel):
    id: UUID
    memory_type: str
    content: str | None = None
    salience: float
    topic_id: int | None = None
    timestamp: str


@router.get("/")
async def root():
    """Root endpoint with application status."""
    return {
        "message": "Memory Palace API",
        "version": "0.1.0",
        "status": "running",
        "features": [
            "discriminated_unions",
            "specification_support",
            "dream_jobs",
            "graph_expansion",
            "ontology_boost"
        ]
    }


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": "2025-08-07T15:20:00Z"
    }


@router.post("/memory/turn", response_model=ConversationTurnResponse)
@with_error_handling(error_level=ErrorLevel.ERROR, reraise=False)
async def store_conversation_turn(
    request: ConversationTurnRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Store a complete conversation turn with relationship detection."""
    try:
        user_memory, assistant_memory = await service.remember_turn(
            user_content=request.user_content,
            assistant_content=request.assistant_content,
            conversation_id=request.conversation_id,
            detect_relationships=request.detect_relationships,
            auto_classify=request.auto_classify
        )

        # Count relationships (placeholder - would need actual implementation)
        relationships_created = 0
        if request.detect_relationships:
            user_relationships = await service.get_memory_relationships(user_memory.id)
            assistant_relationships = await service.get_memory_relationships(assistant_memory.id)
            relationships_created = len(user_relationships) + len(assistant_relationships)

        return ConversationTurnResponse(
            user_memory_id=user_memory.id,
            assistant_memory_id=assistant_memory.id,
            conversation_id=user_memory.conversation_id,
            relationships_created=relationships_created
        )

    except Exception as e:
        logger.error(f"Failed to store conversation turn: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/memory/search", response_model=list[MemoryResponse])
async def search_memories(
    request: SearchRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Search memories using advanced filtering and similarity."""
    try:
        memories = await service.search_memories(
            query=request.query,
            conversation_id=request.conversation_id,
            topic_id=request.topic_id,
            min_salience=request.min_salience,
            limit=request.limit
        )

        # Convert to response format
        return [
            MemoryResponse(
                id=memory.id,
                memory_type=memory.memory_type.value,
                content=getattr(memory, 'content', None),
                salience=getattr(memory, 'salience', 0.0),
                topic_id=getattr(memory, 'topic_id', None),
                timestamp=memory.timestamp.isoformat()
            )
            for memory in memories
        ]

    except Exception as e:
        logger.error(f"Memory search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/memory/recall/{query}")
async def recall_with_graph(
    query: str,
    k: int = 24,
    use_ontology_boost: bool = True,
    service: MemoryService = Depends(get_memory_service)
):
    """Multi-stage recall with ontology boost and graph expansion."""
    try:
        memories = await service.recall_with_graph(
            query=query,
            k=k,
            use_ontology_boost=use_ontology_boost
        )

        return {
            "query": query,
            "results": len(memories),
            "memories": [
                {
                    "id": str(memory.id),
                    "type": memory.memory_type.value,
                    "content": getattr(memory, 'content', None),
                    "topic_id": getattr(memory, 'topic_id', None)
                }
                for memory in memories
            ]
        }

    except Exception as e:
        logger.error(f"Recall failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e