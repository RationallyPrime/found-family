"""Memory API endpoints."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory_palace.api.dependencies import get_memory_service
from memory_palace.domain.models import ConversationTurn, Message
from memory_palace.services.memory_service import MemoryService

router = APIRouter()


class StoreTurnRequest(BaseModel):
    """Request model for storing a conversation turn."""
    user_content: str
    assistant_content: str
    conversation_id: Optional[UUID] = None
    metadata: Optional[dict] = None


class StoreTurnResponse(BaseModel):
    """Response model for storing a turn."""
    turn_id: UUID
    message: str = "Turn stored successfully"


class SearchRequest(BaseModel):
    """Request model for searching memories."""
    query: str
    k: int = 10
    threshold: float = 0.7


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
        turn = await memory_service.store_turn(
            user_content=request.user_content,
            assistant_content=request.assistant_content,
            conversation_id=request.conversation_id,
            metadata=request.metadata,
        )
        return StoreTurnResponse(turn_id=turn.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recall", response_model=SearchResponse)
async def recall_memories(
    request: SearchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> SearchResponse:
    """Search and recall relevant memories."""
    try:
        messages = await memory_service.search_memories(
            query=request.query,
            k=request.k,
            threshold=request.threshold,
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
        
        return SearchResponse(
            messages=message_dicts,
            count=len(messages),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check() -> dict:
    """Check if memory service is healthy."""
    return {"status": "healthy", "service": "memory"}