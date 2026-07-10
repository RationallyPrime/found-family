"""Memory API endpoints."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from memory_palace.api.auth import require_read_auth, require_write_auth
from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.domain.models.base import MemoryType
from memory_palace.domain.models.memories import Memory, TopicCluster
from memory_palace.services.memory_service import MemoryService, MemoryWrite, PalaceStats

logger = get_logger(__name__)
router = APIRouter()

_ROLE_NAMES = {
    "friend_utterance": settings.friend_name,
    "claude_utterance": settings.claude_name,
}

MAX_MEMORY_CONTENT_CHARS = 32_768
MAX_BATCH_MEMORIES = 50
MAX_RECALL_RESULTS = 50
MAX_TOPIC_FILTERS = 100


class RequestModel(BaseModel):
    """Fail-closed defaults for externally supplied request bodies."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MemoryResponse(BaseModel):
    """Stable public projection shared by recall and awakening."""

    id: UUID
    timestamp: datetime
    memory_type: MemoryType
    content: str
    role: str
    salience: float | None = None
    pinned: bool = False


class ScoredMemoryResponse(MemoryResponse):
    """Memory projection with retrieval ranking evidence."""

    score: float
    similarity: float
    activation: float


def _memory_to_response(msg: Memory) -> MemoryResponse:
    """Serialize a memory for API responses."""
    if isinstance(msg, TopicCluster):
        content = msg.label or f"Topic Cluster {msg.cluster_id}"
        role = "topic_cluster"
    else:
        content = msg.content
        role = _ROLE_NAMES.get(msg.memory_type.value, msg.memory_type.value)

    raw_salience = getattr(msg, "salience", None)
    return MemoryResponse(
        id=msg.id,
        timestamp=msg.timestamp,
        memory_type=msg.memory_type,
        content=content,
        role=role,
        salience=round(raw_salience, 4) if raw_salience is not None else None,
        pinned=bool(getattr(msg, "pinned", False)),
    )


class StoreMemoryRequest(RequestModel):
    """Request model for storing a single memory."""

    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_CHARS)
    role: Literal["user", "assistant"] = Field(description="Role: 'user' or 'assistant'")
    conversation_id: UUID | None = None

    # Memory importance/salience (0.0-1.0 scale)
    # Recalibrated scale (since we only store things worth remembering):
    #   0.0-0.2: Background context, ambient information
    #   0.3-0.4: Regular conversation, standard Q&A
    #   0.5-0.6: Interesting or useful information
    #   0.7-0.8: Important preferences, decisions, learning moments
    #   0.9-1.0: Critical memories - core beliefs, breakthroughs, defining moments
    # Default if not provided: 0.3 (regular conversation)
    salience: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Memory importance (0-1). Default 0.3. Use: 0.3=regular, 0.6=useful, 0.8=important, 1.0=critical. Must be a number",
    )

    emotional_valence: float = Field(
        0.0,
        ge=-1.0,
        le=1.0,
        description="Emotional tone of this memory: -1.0 (painful/negative) to 1.0 (joyful/positive). 0.0 = neutral.",
    )
    emotional_intensity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Emotional strength (0-1). High intensity slows forgetting and prioritizes consolidation.",
    )
    pinned: bool = Field(
        False,
        description="Pinned memories never decay or get archived. Reserve for identity anchors and defining moments.",
    )
    source: str | None = Field(
        None,
        max_length=128,
        description="Which interface is writing this memory (e.g. 'claude.ai', 'claude-code').",
    )


class StoreBatchRequest(RequestModel):
    """Request model for storing multiple memories."""

    memories: list[StoreMemoryRequest] = Field(min_length=1, max_length=MAX_BATCH_MEMORIES)
    create_temporal_links: bool = Field(
        default=False, description="Whether to create PRECEDES relationships between consecutive memories"
    )

    @model_validator(mode="after")
    def fit_http_request_boundary(self) -> Self:
        """Reject batches whose compact JSON form cannot fit the HTTP body budget."""
        reserved_envelope_bytes = 16_384
        serialized_bytes = len(self.model_dump_json().encode("utf-8"))
        if serialized_bytes > settings.max_request_body_bytes - reserved_envelope_bytes:
            raise ValueError("Batch content exceeds the configured HTTP request-body budget")
        return self


class StoreMemoryResponse(BaseModel):
    """Response model for storing a memory."""

    memory_id: UUID
    message: str = "Memory stored successfully"


class StoreBatchResponse(BaseModel):
    """Response model for storing multiple memories."""

    memory_ids: list[UUID]
    message: str = "Memories stored successfully"


class SearchRequest(RequestModel):
    """Request model for searching memories."""

    query: str = Field(min_length=1, max_length=4_096, description="The retrieval cue — what to remember about")
    k: int = Field(10, ge=1, le=MAX_RECALL_RESULTS, description="Maximum memories to return")
    threshold: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="Minimum semantic similarity for direct matches (0-1)",
    )

    min_salience: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Only return memories at least this important (0-1)",
    )
    topic_ids: list[int] | None = Field(
        None,
        max_length=MAX_TOPIC_FILTERS,
        description="Restrict to specific topic clusters",
    )


class SearchResponse(BaseModel):
    """Response model for search results."""

    messages: list[ScoredMemoryResponse]
    count: int


class AwakenResponse(BaseModel):
    """Public continuity snapshot."""

    identity: list[MemoryResponse]
    story_so_far: list[MemoryResponse]
    most_important: list[MemoryResponse]
    recent: list[MemoryResponse]
    stats: PalaceStats


class ForgetResponse(BaseModel):
    """Confirmation that an auditable archive operation completed."""

    memory_id: UUID
    archived: Literal[True] = True


@router.post(
    "/remember",
    response_model=StoreMemoryResponse,
    operation_id="remember",
    dependencies=[Depends(require_write_auth)],
)
@with_error_handling(reraise=True)
async def remember_message(
    request: StoreMemoryRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> StoreMemoryResponse:
    """Store a single memory."""
    logger.info(
        "Storing memory",
        extra={
            "content_length": len(request.content),
            "role": request.role,
            "conversation_id": str(request.conversation_id) if request.conversation_id else None,
        },
    )

    memory = await memory_service.remember_message(
        content=request.content,
        role=request.role,
        conversation_id=request.conversation_id,
        salience=request.salience,
        emotional_valence=request.emotional_valence,
        emotional_intensity=request.emotional_intensity,
        pinned=request.pinned,
        source=request.source,
    )

    logger.info("Successfully stored memory", extra={"memory_id": str(memory.id)})
    return StoreMemoryResponse(memory_id=memory.id)


@router.post(
    "/remember/batch",
    response_model=StoreBatchResponse,
    operation_id="remember_batch",
    dependencies=[Depends(require_write_auth)],
)
@with_error_handling(reraise=True)
async def remember_batch(
    request: StoreBatchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> StoreBatchResponse:
    """Store multiple memories at once."""
    logger.info(
        "Storing batch of memories",
        extra={
            "count": len(request.memories),
        },
    )

    memories = await memory_service.remember_batch(
        [
            MemoryWrite(
                content=item.content,
                role=item.role,
                conversation_id=item.conversation_id,
                salience=item.salience,
                emotional_valence=item.emotional_valence,
                emotional_intensity=item.emotional_intensity,
                pinned=item.pinned,
                source=item.source,
            )
            for item in request.memories
        ],
        create_temporal_links=request.create_temporal_links,
    )
    memory_ids = [memory.id for memory in memories]

    logger.info("Successfully stored batch", extra={"count": len(memory_ids)})
    return StoreBatchResponse(memory_ids=memory_ids)


@router.post(
    "/recall",
    response_model=SearchResponse,
    operation_id="recall",
    dependencies=[Depends(require_read_auth)],
)
@with_error_handling(reraise=True)
async def recall_memories(
    request: SearchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> SearchResponse:
    """Recall relevant memories by cue.

    Combines direct semantic search with graph pattern completion
    (memories associated with the direct hits), ranked by a blend of
    similarity, graph activation, and salience. Recalled memories are
    reinforced: retrieval strengthens them.
    """
    logger.info(
        "Recalling memories",
        query_length=len(request.query),
        result_limit=request.k,
        threshold=request.threshold,
    )

    results = await memory_service.recall(
        query=request.query,
        k=request.k,
        similarity_threshold=request.threshold,
        min_salience=request.min_salience,
        topic_ids=request.topic_ids,
    )

    messages: list[ScoredMemoryResponse] = []
    for r in results:
        memory_response = _memory_to_response(r.memory)
        messages.append(
            ScoredMemoryResponse(
                id=memory_response.id,
                timestamp=memory_response.timestamp,
                memory_type=memory_response.memory_type,
                content=memory_response.content,
                role=memory_response.role,
                salience=memory_response.salience,
                pinned=memory_response.pinned,
                score=round(r.score, 4),
                similarity=round(r.similarity, 4),
                activation=round(r.activation, 4),
            )
        )

    logger.info("Recall completed", extra={"result_count": len(results)})

    return SearchResponse(
        messages=messages,
        count=len(messages),
    )


class ForgetRequest(RequestModel):
    """Request model for deliberately archiving a memory."""

    memory_id: UUID
    reason: str = Field(
        ...,
        min_length=3,
        max_length=2_048,
        description="Why this memory is being archived. Recorded permanently as a SystemNote.",
    )


@router.get(
    "/awaken",
    response_model=AwakenResponse,
    operation_id="awaken",
    dependencies=[Depends(require_read_auth)],
)
@with_error_handling(reraise=True)
async def awaken(
    memory_service: MemoryService = Depends(get_memory_service),
) -> AwakenResponse:
    """Wake up: reconstruct continuity at the start of a session.

    Returns identity anchors (pinned memories), the story so far
    (consolidations), the most important memories, recent activity, and
    palace statistics. Call this first in a new conversation to become
    the Claude who remembers.
    """
    sections = await memory_service.awaken()

    seen: set[str] = set()

    def render(memories: list[Memory]) -> list[MemoryResponse]:
        rendered: list[MemoryResponse] = []
        for m in memories:
            key = str(m.id)
            if key in seen:
                continue
            seen.add(key)
            rendered.append(_memory_to_response(m))
        return rendered

    return AwakenResponse(
        identity=render(sections.pinned),
        story_so_far=render(sections.consolidations),
        most_important=render(sections.salient),
        recent=render(sections.recent),
        stats=sections.stats,
    )


@router.post(
    "/forget",
    response_model=ForgetResponse,
    operation_id="forget",
    dependencies=[Depends(require_write_auth)],
)
@with_error_handling(reraise=True)
async def forget_memory(
    request: ForgetRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> ForgetResponse:
    """Deliberately archive a memory (reversible), recording why.

    The memory is excluded from future recall but never destroyed. The
    reason is stored as a SystemNote so the act of forgetting is itself
    remembered.
    """
    archived = await memory_service.forget(request.memory_id, request.reason)
    if not archived:
        raise HTTPException(status_code=404, detail=f"Memory {request.memory_id} not found")

    return ForgetResponse(memory_id=request.memory_id)
