from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from .base import GraphModel, MemoryType


class UserUtterance(GraphModel):
    """User's input in a conversation."""
    memory_type: Literal[MemoryType.USER_UTTERANCE] = MemoryType.USER_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    salience: float = 0.5
    conversation_id: UUID | None = None
    
    def __str__(self) -> str:
        return f"UserUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class AssistantUtterance(GraphModel):
    """Assistant's response in a conversation."""
    memory_type: Literal[MemoryType.ASSISTANT_UTTERANCE] = MemoryType.ASSISTANT_UTTERANCE
    content: str
    embedding: list[float] | None = None
    topic_id: int | None = None
    salience: float = 0.5
    conversation_id: UUID | None = None
    
    def __str__(self) -> str:
        return f"AssistantUtterance(content='{self.content[:50]}...', topic={self.topic_id})"


class SystemNote(GraphModel):
    """System-generated notes and observations."""
    memory_type: Literal[MemoryType.SYSTEM_NOTE] = MemoryType.SYSTEM_NOTE
    content: str
    note_type: str = "general"  # categorize system notes
    related_memory_ids: list[UUID] = Field(default_factory=list)
    
    def __str__(self) -> str:
        return f"SystemNote(type={self.note_type}, content='{self.content[:30]}...')"


class ConversationTurn(GraphModel):
    """A complete turn in conversation (user + assistant pair)."""
    memory_type: Literal[MemoryType.CONVERSATION_TURN] = MemoryType.CONVERSATION_TURN
    user_utterance_id: UUID
    assistant_utterance_id: UUID
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


# The discriminated union - Pydantic will automatically route based on memory_type
Memory = Annotated[
    UserUtterance | AssistantUtterance | SystemNote | ConversationTurn | TopicCluster | OntologyNode | MemoryRelationship,
    Field(discriminator="memory_type")
]

# Type aliases for convenience
Turn = tuple[UserUtterance, AssistantUtterance]
MemoryPair = UserUtterance | AssistantUtterance
