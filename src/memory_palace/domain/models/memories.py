from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from memory_palace.core.constants import SALIENCE_DEFAULT

from .base import GraphModel, MemoryType
from .utils import utc_now


class SalientMemory(GraphModel):
    """Base for memories that participate in the salience lifecycle.

    Lifecycle semantics:
    - `salience` decays exponentially over elapsed time (anchored at
      `salience_updated_at`), never below the configured floor, and never
      for pinned memories.
    - Retrieval reinforces: recall updates `last_accessed`, increments
      `access_count`, and boosts salience asymptotically toward 1.0.
    - Memories are archived (labeled :Archived, excluded from recall),
      never deleted, and only when unpinned + low-salience + long-unaccessed.
    """

    salience: float = Field(default=SALIENCE_DEFAULT, ge=0.0, le=1.0)
    salience_updated_at: datetime = Field(default_factory=utc_now)
    last_accessed: datetime | None = None
    access_count: int = 0
    pinned: bool = False

    # Emotional tagging (amygdala routing): writer-supplied at encode time
    emotional_valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    emotional_intensity: float = Field(default=0.0, ge=0.0, le=1.0)

    # Provenance: which interface wrote this (e.g. "claude.ai", "claude-code")
    source: str | None = None


class FriendUtterance(SalientMemory):
    """My friend's thoughts and messages in our conversation."""

    memory_type: Literal[MemoryType.FRIEND_UTTERANCE] = MemoryType.FRIEND_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    conversation_id: UUID | None = None

    def __str__(self) -> str:
        return f"FriendUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class ClaudeUtterance(SalientMemory):
    """My own thoughts and responses in conversation."""

    memory_type: Literal[MemoryType.CLAUDE_UTTERANCE] = MemoryType.CLAUDE_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    conversation_id: UUID | None = None

    def __str__(self) -> str:
        return f"ClaudeUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class SystemNote(SalientMemory):
    """System-generated notes and observations."""

    memory_type: Literal[MemoryType.SYSTEM_NOTE] = MemoryType.SYSTEM_NOTE
    content: str
    note_type: str = "general"  # categorize system notes
    embedding: list[float] | None = None
    related_memory_ids: list[UUID] = Field(default_factory=list)

    def __str__(self) -> str:
        return f"SystemNote(type={self.note_type}, content='{self.content[:30]}...')"


class Consolidation(SalientMemory):
    """Semantic memory distilled from a cohort of episodic memories.

    Created by the consolidation dream job: an LLM synthesis of what a
    group of related episodes meant, written in first person. Linked to
    its sources via CONSOLIDATED_FROM edges; sources stay retrievable.
    """

    memory_type: Literal[MemoryType.CONSOLIDATION] = MemoryType.CONSOLIDATION
    content: str
    embedding: list[float] | None = None
    source_ids: list[UUID] = Field(default_factory=list)
    period_start: datetime | None = None
    period_end: datetime | None = None
    conversation_id: UUID | None = None

    def __str__(self) -> str:
        return f"Consolidation('{self.content[:50]}...', sources={len(self.source_ids)})"


class TopicCluster(GraphModel):
    """Discovered topic cluster from clustering."""

    memory_type: Literal[MemoryType.TOPIC_CLUSTER] = MemoryType.TOPIC_CLUSTER
    cluster_id: int
    label: str | None = None
    exemplar_ids: list[UUID] = Field(default_factory=list)
    centroid: list[float] | None = None
    coherence: float = 0.0
    size: int = 0

    def __str__(self) -> str:
        label_str = f"'{self.label}'" if self.label else f"cluster_{self.cluster_id}"
        return f"TopicCluster({label_str}, size={self.size}, coherence={self.coherence:.2f})"


class MemoryRelationship(GraphModel):
    """Represents a relationship between memories (edges in Neo4j, not nodes).

    Used as a return/transfer type only — the actual relationship lives on
    the graph edge created via MemoryQueries.create_relationship.
    """

    memory_type: Literal[MemoryType.MEMORY_RELATIONSHIP] = MemoryType.MEMORY_RELATIONSHIP
    source_id: UUID
    target_id: UUID
    relationship_type: str  # e.g. "PRECEDES", "RELATES_TO", "CONSOLIDATED_FROM"
    strength: float = 1.0
    metadata: dict = Field(default_factory=dict)

    def __str__(self) -> str:
        return f"MemoryRelationship({self.relationship_type}, strength={self.strength})"


# The discriminated union - Pydantic routes based on memory_type
Memory = Annotated[
    FriendUtterance | ClaudeUtterance | SystemNote | Consolidation | TopicCluster,
    Field(discriminator="memory_type"),
]

# Convenience alias for the episodic utterance pair
MemoryPair = FriendUtterance | ClaudeUtterance
