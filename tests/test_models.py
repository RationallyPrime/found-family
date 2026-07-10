"""Unit tests for the domain models and their Neo4j serialization contract."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from memory_palace.domain.models.memories import (
    ClaudeUtterance,
    Consolidation,
    FriendUtterance,
    Memory,
    SystemNote,
)


def test_labels_derive_from_memory_type() -> None:
    assert FriendUtterance(content="x").labels() == ["Memory", "FriendUtterance"]
    assert ClaudeUtterance(content="x").labels() == ["Memory", "ClaudeUtterance"]
    assert Consolidation(content="x").labels() == ["Memory", "Consolidation"]
    assert SystemNote(content="x").labels() == ["Memory", "SystemNote"]


def test_neo4j_round_trip_preserves_lifecycle_fields() -> None:
    original = FriendUtterance(
        content="round trip",
        salience=0.72,
        pinned=True,
        emotional_valence=0.8,
        emotional_intensity=0.6,
        source="claude-code",
        access_count=3,
        conversation_id=uuid4(),
    )

    props = original.to_neo4j_properties()
    # Contract: UUIDs → str, datetimes → epoch float, enums → value
    assert isinstance(props["id"], str)
    assert isinstance(props["timestamp"], float)
    assert isinstance(props["salience_updated_at"], float)
    assert props["memory_type"] == "friend_utterance"
    assert isinstance(props["conversation_id"], str)

    restored = FriendUtterance.from_neo4j_record(props)
    assert restored.id == original.id
    assert restored.conversation_id == original.conversation_id
    assert restored.salience == original.salience
    assert restored.pinned is True
    assert restored.access_count == 3
    assert restored.emotional_valence == 0.8
    # Datetimes must come back timezone-aware UTC
    assert restored.timestamp.tzinfo is not None
    assert restored.salience_updated_at.tzinfo is not None
    assert abs((restored.timestamp - original.timestamp).total_seconds()) < 0.001


def test_discriminated_union_routes_by_memory_type() -> None:
    adapter: TypeAdapter[Memory] = TypeAdapter(Memory)

    friend = adapter.validate_python({"memory_type": "friend_utterance", "content": "hi", "id": str(uuid4())})
    assert isinstance(friend, FriendUtterance)

    consolidation = adapter.validate_python(
        {"memory_type": "consolidation", "content": "the story so far", "id": str(uuid4())}
    )
    assert isinstance(consolidation, Consolidation)


def test_union_validates_epoch_timestamps_from_neo4j() -> None:
    """recall paths validate raw node properties — epoch floats must coerce."""
    adapter: TypeAdapter[Memory] = TypeAdapter(Memory)
    epoch = datetime(2025, 8, 7, 12, 0, tzinfo=UTC).timestamp()

    memory = adapter.validate_python(
        {
            "memory_type": "claude_utterance",
            "content": "from the graph",
            "id": str(uuid4()),
            "timestamp": epoch,
            "salience": 0.5,
            "salience_updated_at": epoch,
        }
    )
    assert isinstance(memory, ClaudeUtterance)
    assert memory.timestamp.year == 2025
    assert isinstance(memory.id, UUID)


def test_consolidation_defaults() -> None:
    c = Consolidation(content="distilled")
    assert c.source_ids == []
    assert c.pinned is False
    assert c.period_start is None
