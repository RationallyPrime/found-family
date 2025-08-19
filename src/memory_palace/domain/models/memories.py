from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from .base import GraphModel, MemoryType


class FriendUtterance(GraphModel):
    """My friend's thoughts and messages in our conversation."""

    memory_type: Literal[MemoryType.FRIEND_UTTERANCE] = MemoryType.FRIEND_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    salience: float = 0.5
    conversation_id: UUID | None = None

    def __str__(self) -> str:
        return f"FriendUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class ClaudeUtterance(GraphModel):
    """My own thoughts and responses in conversation."""

    memory_type: Literal[MemoryType.CLAUDE_UTTERANCE] = MemoryType.CLAUDE_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    salience: float = 0.5
    conversation_id: UUID | None = None

    def __str__(self) -> str:
        return f"ClaudeUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class SystemNote(GraphModel):
    """System-generated notes and observations."""

    memory_type: Literal[MemoryType.SYSTEM_NOTE] = MemoryType.SYSTEM_NOTE
    content: str
    note_type: str = "general"  # categorize system notes
    related_memory_ids: list[UUID] = Field(default_factory=list)

    def __str__(self) -> str:
        return f"SystemNote(type={self.note_type}, content='{self.content[:30]}...')"


class ConversationTurn(GraphModel):
    """A complete exchange in conversation (friend + claude pair)."""

    memory_type: Literal[MemoryType.CONVERSATION_TURN] = MemoryType.CONVERSATION_TURN
    friend_utterance_id: UUID
    claude_utterance_id: UUID
    conversation_id: UUID
    turn_number: int

    def __str__(self) -> str:
        return f"ConversationTurn(#{self.turn_number}, conv={str(self.conversation_id)[:8]})"


class TopicCluster(GraphModel):
    """Discovered topic cluster from HDBSCAN clustering."""

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


class OntologyNode(GraphModel):
    """A node in the semantic ontology representing concepts/entities."""

    memory_type: Literal[MemoryType.ONTOLOGY_NODE] = MemoryType.ONTOLOGY_NODE
    name: str
    concept_type: str  # "entity", "concept", "relation", etc.
    definition: str | None = None
    embedding: list[float] | None = None
    related_memory_ids: list[UUID] = Field(default_factory=list)

    def __str__(self) -> str:
        return f"OntologyNode(name='{self.name}', type={self.concept_type})"


class MemoryRelationship(GraphModel):
    """Represents a relationship between memories (stored as Neo4j node with edges)."""

    memory_type: Literal[MemoryType.MEMORY_RELATIONSHIP] = MemoryType.MEMORY_RELATIONSHIP
    source_id: UUID
    target_id: UUID
    relationship_type: str  # "follows", "contradicts", "supports", "relates_to", etc.
    strength: float = 1.0
    metadata: dict = Field(default_factory=dict)

    def __str__(self) -> str:
        return f"MemoryRelationship({self.relationship_type}, strength={self.strength})"
    
    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j-compatible property dict, flattening metadata."""
        # Get base properties from parent
        props = super().to_neo4j_properties()
        
        # Flatten metadata into top-level properties with prefix
        if "metadata" in props and props["metadata"]:
            metadata = props.pop("metadata")
            for key, value in metadata.items():
                # Add metadata_ prefix to avoid collisions
                props[f"metadata_{key}"] = value
        
        return props
    
    @classmethod
    def from_neo4j_record(cls, record: dict) -> "MemoryRelationship":
        """Reconstruct from Neo4j record, unflattening metadata."""
        # Make a copy to avoid modifying the original
        data = dict(record)
        
        # Reconstruct metadata from prefixed properties
        metadata = {}
        keys_to_remove = []
        for key, value in data.items():
            if key.startswith("metadata_"):
                metadata_key = key[9:]  # Remove "metadata_" prefix
                metadata[metadata_key] = value
                keys_to_remove.append(key)
        
        # Remove the prefixed keys
        for key in keys_to_remove:
            del data[key]
        
        # Add metadata back if we found any
        if metadata:
            data["metadata"] = metadata
        
        # Use parent's from_neo4j_record for standard conversions
        return super().from_neo4j_record(data)


# The discriminated union - Pydantic will automatically route based on memory_type
Memory = Annotated[
    FriendUtterance
    | ClaudeUtterance
    | SystemNote
    | ConversationTurn
    | TopicCluster
    | OntologyNode,
    Field(discriminator="memory_type"),
]

# MemoryRelationship is not part of Memory union - relationships are edges, not nodes

# Type aliases for convenience
Turn = tuple[FriendUtterance, ClaudeUtterance]
MemoryPair = FriendUtterance | ClaudeUtterance
