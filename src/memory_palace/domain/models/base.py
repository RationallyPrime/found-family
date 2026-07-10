import contextlib
import typing
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum, StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from memory_palace.domain.models.utils import utc_now


class MemoryType(StrEnum):
    """Registry of all memory types in the palace."""

    # Core memories
    FRIEND_UTTERANCE = "friend_utterance"  # The human I'm talking with
    CLAUDE_UTTERANCE = "claude_utterance"  # My own thoughts and responses
    SYSTEM_NOTE = "system_note"

    # Derived memories
    CONSOLIDATION = "consolidation"  # Semantic memory distilled from episodes
    TOPIC_CLUSTER = "topic_cluster"

    # Relationships (transfer type only - stored as edges in Neo4j)
    MEMORY_RELATIONSHIP = "memory_relationship"


class GraphModel(BaseModel):
    """Base class for all Neo4j entities with discriminated union support.

    Serialization contract with Neo4j:
    - UUIDs are stored as strings
    - datetimes are stored as UTC epoch floats
    - enums are stored as their values
    """

    id: UUID = Field(default_factory=uuid4)
    memory_type: MemoryType
    timestamp: datetime = Field(default_factory=utc_now)

    @classmethod
    def labels(cls) -> list[str]:
        """Get Neo4j labels for this entity."""
        # Try to get the memory_type from model fields
        if hasattr(cls, "model_fields") and "memory_type" in cls.model_fields:
            field_info = cls.model_fields["memory_type"]
            if hasattr(field_info, "default") and isinstance(field_info.default, MemoryType):
                memory_type = field_info.default
                # Convert enum value to PascalCase for Neo4j labels
                pascal_case = "".join(part.capitalize() for part in memory_type.value.split("_"))
                return ["Memory", pascal_case]

        # Fallback to class name
        return ["Memory", cls.__name__]

    @classmethod
    def _datetime_fields(cls) -> set[str]:
        """Field names whose annotation is datetime (or datetime | None)."""
        fields: set[str] = set()
        for name, info in cls.model_fields.items():
            annotation = info.annotation
            if annotation is datetime or datetime in typing.get_args(annotation):
                fields.add(name)
        return fields

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j-compatible property dict."""
        props = self.model_dump()

        for key, value in props.items():
            if isinstance(value, UUID):
                props[key] = str(value)
            elif isinstance(value, datetime):
                props[key] = value.timestamp()
            elif isinstance(value, Enum):
                props[key] = value.value
            elif isinstance(value, list) and value and isinstance(value[0], UUID):
                props[key] = [str(v) for v in value]

        return props

    @classmethod
    def from_neo4j_record(cls, record: Mapping[str, object]) -> typing.Self:
        """Create instance from Neo4j record."""
        record = dict(record)

        # Convert epoch floats back to aware UTC datetimes
        for field in cls._datetime_fields():
            match record.get(field):
                case int() | float() as epoch:
                    record[field] = datetime.fromtimestamp(epoch, tz=UTC)

        # Convert memory_type string back to enum
        if "memory_type" in record and isinstance(record["memory_type"], str):
            record["memory_type"] = MemoryType(record["memory_type"])

        # Convert string UUIDs back to UUID objects
        if "id" in record and isinstance(record["id"], str):
            record["id"] = UUID(record["id"])
        for key, value in record.items():
            if key.endswith("_id") and isinstance(value, str):
                with contextlib.suppress(ValueError):
                    record[key] = UUID(value)

        return cls.model_validate(record)
