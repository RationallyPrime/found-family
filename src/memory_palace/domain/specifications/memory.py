"""Memory-specific specifications for querying the palace.

These specifications enable complex, composable queries that respect
the ontology and relationships between memories.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from memory_palace.domain.models.ontology import MemoryRole, RelationType
from memory_palace.domain.models.utils import utc_now
from memory_palace.domain.specifications.composite import BaseSpecification


class SalientMemorySpecification(BaseSpecification):
    """Find memories above a salience threshold."""

    def __init__(self, min_salience: float = 0.5):
        self.min_salience = min_salience

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "salience") and entity.salience >= self.min_salience

    def to_filter(self) -> dict[str, Any]:
        return {"salience__gte": self.min_salience}

    def to_cypher(self) -> str:
        """Generate Cypher WHERE clause."""
        return f"m.salience >= {self.min_salience}"


class TopicMemorySpecification(BaseSpecification):
    """Find memories belonging to specific topics."""

    def __init__(self, topic_ids: list[int] | int):
        self.topic_ids = topic_ids if isinstance(topic_ids, list) else [topic_ids]

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "topic_id") and entity.topic_id in self.topic_ids

    def to_filter(self) -> dict[str, Any]:
        return {"topic_id__in": self.topic_ids}

    def to_cypher(self) -> str:
        return f"m.topic_id IN {self.topic_ids}"


class ConversationMemorySpecification(BaseSpecification):
    """Find memories from a specific conversation."""

    def __init__(self, conversation_id: UUID):
        self.conversation_id = conversation_id

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "conversation_id") and entity.conversation_id == self.conversation_id

    def to_filter(self) -> dict[str, Any]:
        return {"conversation_id": str(self.conversation_id)}

    def to_cypher(self) -> str:
        return f"m.conversation_id = '{self.conversation_id}'"


class RecentMemorySpecification(BaseSpecification):
    """Find memories within a time window."""

    def __init__(self, days: int = 7, hours: int = 0):
        self.cutoff = utc_now() - timedelta(days=days, hours=hours)

    def is_satisfied_by(self, entity: Any) -> bool:
        if not hasattr(entity, "timestamp"):
            return False
        return entity.timestamp >= self.cutoff

    def to_filter(self) -> dict[str, Any]:
        return {"timestamp__gte": self.cutoff.isoformat()}

    def to_cypher(self) -> str:
        return f"m.timestamp >= datetime('{self.cutoff.isoformat()}')"


class EmotionalMemorySpecification(BaseSpecification):
    """Find memories with specific emotional characteristics."""

    def __init__(self, min_intensity: float = 0.5, valence_range: tuple[float, float] = (-1.0, 1.0)):
        self.min_intensity = min_intensity
        self.valence_range = valence_range

    def is_satisfied_by(self, entity: Any) -> bool:
        if not hasattr(entity, "emotional_intensity") or not hasattr(entity, "emotional_valence"):
            return False

        return (
            entity.emotional_intensity >= self.min_intensity
            and self.valence_range[0] <= entity.emotional_valence <= self.valence_range[1]
        )

    def to_filter(self) -> dict[str, Any]:
        return {
            "emotional_intensity__gte": self.min_intensity,
            "emotional_valence__gte": self.valence_range[0],
            "emotional_valence__lte": self.valence_range[1],
        }

    def to_cypher(self) -> str:
        return (
            f"m.emotional_intensity >= {self.min_intensity} AND "
            f"m.emotional_valence >= {self.valence_range[0]} AND "
            f"m.emotional_valence <= {self.valence_range[1]}"
        )


class OntologyPathSpecification(BaseSpecification):
    """Find memories in a specific ontology path."""

    def __init__(self, path_prefix: list[str]):
        self.path_prefix = path_prefix

    def is_satisfied_by(self, entity: Any) -> bool:
        if not hasattr(entity, "ontology_path"):
            return False

        entity_path = entity.ontology_path
        if len(entity_path) < len(self.path_prefix):
            return False

        return entity_path[: len(self.path_prefix)] == self.path_prefix

    def to_filter(self) -> dict[str, Any]:
        # This needs custom handling in the query layer
        return {"ontology_path__startswith": self.path_prefix}

    def to_cypher(self) -> str:
        # Cypher list comparison
        path_str = str(self.path_prefix).replace("'", '"')
        return f"m.ontology_path[0..{len(self.path_prefix)}] = {path_str}"


class RelatedMemorySpecification(BaseSpecification):
    """Find memories related to a source memory."""

    def __init__(
        self, source_id: UUID, relationship_types: list[RelationType] | None = None, min_strength: float = 0.0
    ):
        self.source_id = source_id
        self.relationship_types = relationship_types
        self.min_strength = min_strength

    def is_satisfied_by(self, entity: Any) -> bool:  # noqa: ARG002
        # This requires graph traversal, not simple property check
        return False

    def to_filter(self) -> dict[str, Any]:
        return {
            "$graph_traverse": {
                "source": str(self.source_id),
                "relationships": [r.value for r in self.relationship_types] if self.relationship_types else None,
                "min_strength": self.min_strength,
            }
        }

    def to_cypher(self) -> str:
        rel_filter = ""
        if self.relationship_types:
            rel_types = "|".join(r.value for r in self.relationship_types)
            rel_filter = f":{rel_types}"

        strength_filter = ""
        if self.min_strength > 0:
            strength_filter = f" WHERE r.strength >= {self.min_strength}"

        return f"""
        MATCH (source:Message {{id: '{self.source_id}'}})
        MATCH (source)-[r{rel_filter}]->(m:Message)
        {strength_filter}
        """


class FrequentlyAccessedSpecification(BaseSpecification):
    """Find frequently accessed memories."""

    def __init__(self, min_access_count: int = 5):
        self.min_access_count = min_access_count

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "access_count") and entity.access_count >= self.min_access_count

    def to_filter(self) -> dict[str, Any]:
        return {"access_count__gte": self.min_access_count}

    def to_cypher(self) -> str:
        return f"m.access_count >= {self.min_access_count}"


class UserMemorySpecification(BaseSpecification):
    """Find memories from the user."""

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "role") and entity.role == MemoryRole.USER

    def to_filter(self) -> dict[str, Any]:
        return {"role": MemoryRole.USER.value}

    def to_cypher(self) -> str:
        return f"m.role = '{MemoryRole.USER.value}'"


class AssistantMemorySpecification(BaseSpecification):
    """Find memories from the assistant."""

    def is_satisfied_by(self, entity: Any) -> bool:
        return hasattr(entity, "role") and entity.role == MemoryRole.ASSISTANT

    def to_filter(self) -> dict[str, Any]:
        return {"role": MemoryRole.ASSISTANT.value}

    def to_cypher(self) -> str:
        return f"m.role = '{MemoryRole.ASSISTANT.value}'"


class ConceptMemorySpecification(BaseSpecification):
    """Find memories containing specific concepts."""

    def __init__(self, concepts: list[str]):
        self.concepts = concepts

    def is_satisfied_by(self, entity: Any) -> bool:
        if not hasattr(entity, "concepts"):
            return False

        entity_concepts = set(entity.concepts)
        search_concepts = set(self.concepts)
        return bool(entity_concepts & search_concepts)

    def to_filter(self) -> dict[str, Any]:
        return {"concepts__overlap": self.concepts}

    def to_cypher(self) -> str:
        # Check if any concept in the search list exists in the memory's concepts
        concept_checks = [f"'{c}' IN m.concepts" for c in self.concepts]
        return f"({' OR '.join(concept_checks)})"


class DecayingMemorySpecification(BaseSpecification):
    """Find memories that are decaying (haven't been accessed recently)."""

    def __init__(self, days_since_access: int = 30, max_salience: float = 0.3):
        self.cutoff = utc_now() - timedelta(days=days_since_access)
        self.max_salience = max_salience

    def is_satisfied_by(self, entity: Any) -> bool:
        if not hasattr(entity, "last_accessed") or not hasattr(entity, "salience"):
            return False

        is_old = entity.last_accessed is None or entity.last_accessed < self.cutoff
        is_low_salience = entity.salience <= self.max_salience

        return is_old and is_low_salience

    def to_filter(self) -> dict[str, Any]:
        return {
            "$or": [{"last_accessed": None}, {"last_accessed__lt": self.cutoff.isoformat()}],
            "salience__lte": self.max_salience,
        }

    def to_cypher(self) -> str:
        return f"""(m.last_accessed IS NULL OR m.last_accessed < datetime('{self.cutoff.isoformat()}'))
        AND m.salience <= {self.max_salience}"""
