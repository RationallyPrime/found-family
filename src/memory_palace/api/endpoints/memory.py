"""Memory API endpoints."""

import traceback
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.logging import get_logger
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
        raise HTTPException(status_code=500, detail=str(e))


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
            # Note: threshold isn't used in search_memories, could add similarity filtering later
        )

        # Convert to dict for response
        message_dicts = [
            {
                "id": str(msg.id),
                "role": msg.role.value,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
            }
            for msg in messages
        ]

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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check() -> dict:
    """Check if memory service is healthy."""
    return {"status": "healthy", "service": "memory"}
