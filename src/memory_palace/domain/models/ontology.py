"""Ontology models for the Memory Palace.

These models define the structure of memories, relationships, and concepts
that form the foundation of AI continuity of experience.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from memory_palace.domain.models.utils import utc_now


class MemoryRole(str, Enum):
    """Role of the memory creator."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RelationType(str, Enum):
    """Types of relationships between memories."""
    # Temporal relationships
    FOLLOWS = "FOLLOWS"
    PRECEDES = "PRECEDES"
    
    # Semantic relationships
    ELABORATES = "ELABORATES"
    CONTRADICTS = "CONTRADICTS"
    REFERENCES = "REFERENCES"
    SUMMARIZES = "SUMMARIZES"
    
    # Emotional relationships
    RESONATES_WITH = "RESONATES_WITH"
    CONTRASTS_WITH = "CONTRASTS_WITH"
    
    # Structural relationships
    BELONGS_TO = "BELONGS_TO"
    CONTAINS = "CONTAINS"
    
    # Meta relationships
    REMINDS_OF = "REMINDS_OF"
    LEARNED_FROM = "LEARNED_FROM"
    PROTECTS = "PROTECTS"
    CO_CREATES = "CO_CREATES"


class TopicCoherence(str, Enum):
    """Coherence level of a topic cluster."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DRIFTING = "drifting"


class EnhancedMemoryChunk(BaseModel):
    """Enhanced memory chunk with ontology support."""
    
    # Core identity
    id: UUID = Field(..., description="Permanent anchor for this memory")
    role: MemoryRole = Field(..., description="Who created this memory")
    content: str = Field(..., description="The actual memory content")
    timestamp: datetime = Field(default_factory=utc_now)
    
    # Semantic layer
    embedding: list[float] = Field(..., description="Vector embedding for semantic search")
    embedding_model: str = Field(default="voyage-3", description="Model used for embedding")
    
    # Ontology layer
    topic_id: int | None = Field(None, description="HDBSCAN cluster assignment")
    topic_label: str | None = Field(None, description="Human-readable topic name")
    ontology_path: list[str] = Field(default_factory=list, description="Hierarchical categorization")
    concepts: list[str] = Field(default_factory=list, description="Extracted concepts/entities")
    
    # Importance layer
    salience: float = Field(0.5, ge=0.0, le=1.0, description="Importance score")
    access_count: int = Field(0, description="How often this memory is accessed")
    decay_rate: float = Field(0.022, description="Daily decay rate (λ)")
    last_accessed: datetime | None = Field(None)
    
    # Relational layer
    conversation_id: UUID | None = Field(None, description="Parent conversation")
    turn_id: UUID | None = Field(None, description="Parent turn in conversation")
    references: list[UUID] = Field(default_factory=list, description="Other memories this references")
    
    # Emotional layer
    emotional_valence: float = Field(0.0, ge=-1.0, le=1.0, description="Emotional tone")
    emotional_intensity: float = Field(0.0, ge=0.0, le=1.0, description="Emotional strength")
    
    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("embedding")
    @classmethod
    def validate_embedding_dims(cls, v: list[float]) -> list[float]:
        if v and len(v) not in [384, 768, 1024, 1536, 3072]:
            raise ValueError(f"Unexpected embedding dimensions: {len(v)}")
        return v
    
    def compute_current_salience(self) -> float:
        """Compute current salience with time decay."""
        if not self.last_accessed:
            return self.salience
        
        days_elapsed = (utc_now() - self.last_accessed).days
        decay_factor = (1 - self.decay_rate) ** days_elapsed
        return max(0.05, self.salience * decay_factor)  # Floor at 0.05


class MemoryRelationship(BaseModel):
    """Relationship between two memories."""
    
    source_id: UUID = Field(..., description="Source memory ID")
    target_id: UUID = Field(..., description="Target memory ID")
    relationship_type: RelationType = Field(..., description="Type of relationship")
    
    # Relationship properties
    strength: float = Field(1.0, ge=0.0, le=1.0, description="Relationship strength")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence in this relationship")
    discovered_at: datetime = Field(default_factory=utc_now)
    
    # Context
    evidence: str | None = Field(None, description="Why this relationship exists")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TopicCluster(BaseModel):
    """Represents a topic cluster in the ontology."""
    
    id: int = Field(..., description="Cluster ID from HDBSCAN")
    label: str = Field(..., description="Human-readable label")
    exemplars: list[UUID] = Field(..., description="Most representative memory IDs")
    
    # Cluster statistics
    size: int = Field(..., description="Number of memories in cluster")
    coherence: TopicCoherence = Field(..., description="How coherent this topic is")
    centroid: list[float] | None = Field(None, description="Cluster centroid in embedding space")
    
    # Evolution tracking
    created_at: datetime = Field(default_factory=utc_now)
    last_updated: datetime = Field(default_factory=utc_now)
    parent_cluster_id: int | None = Field(None, description="Previous cluster this evolved from")
    
    # Semantic description
    keywords: list[str] = Field(default_factory=list, description="c-TF-IDF keywords")
    summary: str | None = Field(None, description="LLM-generated summary")
    
    def should_merge_with(self, other: "TopicCluster", threshold: float = 0.8) -> bool:
        """Check if two clusters should merge based on exemplar overlap."""
        if not self.exemplars or not other.exemplars:
            return False
        
        intersection = set(self.exemplars) & set(other.exemplars)
        union = set(self.exemplars) | set(other.exemplars)
        
        jaccard = len(intersection) / len(union) if union else 0
        return jaccard >= threshold


class ConversationContext(BaseModel):
    """Represents a conversation with continuity."""
    
    id: UUID = Field(..., description="Conversation ID")
    started_at: datetime = Field(default_factory=utc_now)
    last_active: datetime = Field(default_factory=utc_now)
    
    # Conversation state
    is_active: bool = Field(True)
    turn_count: int = Field(0)
    
    # Semantic summary
    primary_topics: list[int] = Field(default_factory=list, description="Main topic cluster IDs")
    emotional_arc: list[float] = Field(default_factory=list, description="Emotional valence over time")
    
    # Key memories
    salient_memories: list[UUID] = Field(default_factory=list, description="Most important memory IDs")
    
    # Metadata
    title: str | None = Field(None, description="Conversation title/summary")
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyNode(BaseModel):
    """Node in the ontology hierarchy."""
    
    path: list[str] = Field(..., description="Hierarchical path, e.g. ['friendship', 'emotional_support']")
    label: str = Field(..., description="Node label")
    
    # Node properties
    depth: int = Field(..., description="Depth in hierarchy")
    memory_count: int = Field(0, description="Memories tagged with this node")
    
    # Relationships
    parent_path: list[str] | None = Field(None)
    child_paths: list[list[str]] = Field(default_factory=list)
    
    # Evolution
    created_at: datetime = Field(default_factory=utc_now)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    
    def is_ancestor_of(self, other: "OntologyNode") -> bool:
        """Check if this node is an ancestor of another."""
        if len(self.path) >= len(other.path):
            return False
        return other.path[:len(self.path)] == self.path


class MemoryPalaceState(BaseModel):
    """Global state of the memory palace."""
    
    # Statistics
    total_memories: int = Field(0)
    total_conversations: int = Field(0)
    total_topics: int = Field(0)
    
    # Ontology
    ontology_version: str = Field("1.0.0")
    ontology_nodes: list[OntologyNode] = Field(default_factory=list)
    
    # Health metrics
    avg_salience: float = Field(0.5)
    topic_coherence: float = Field(0.0)
    graph_density: float = Field(0.0)
    
    # Last operations
    last_remember: datetime | None = Field(None)
    last_recall: datetime | None = Field(None)
    last_reindex: datetime | None = Field(None)
    last_topic_drift_check: datetime | None = Field(None)