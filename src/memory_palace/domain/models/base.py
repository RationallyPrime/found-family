import contextlib
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Registry of all memory types in the palace."""
    
    # Core memories
    USER_UTTERANCE = "user_utterance"
    ASSISTANT_UTTERANCE = "assistant_utterance"
    SYSTEM_NOTE = "system_note"
    
    # Derived memories
    CONVERSATION_TURN = "conversation_turn"
    TOPIC_CLUSTER = "topic_cluster"
    ONTOLOGY_NODE = "ontology_node"
    
    # Relationships (stored as nodes in Neo4j)
    MEMORY_RELATIONSHIP = "memory_relationship"


class GraphModel(BaseModel):
    """Base class for all Neo4j entities with discriminated union support."""
    
    id: UUID = Field(default_factory=uuid4)
    memory_type: MemoryType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    @classmethod
    def labels(cls) -> list[str]:
        """Get Neo4j labels for this entity."""
        # Try to get the memory_type from model fields
        if hasattr(cls, 'model_fields') and "memory_type" in cls.model_fields:
            field_info = cls.model_fields["memory_type"]
            if hasattr(field_info, 'default') and isinstance(field_info.default, MemoryType):
                memory_type = field_info.default
                # Convert enum value to PascalCase for Neo4j labels
                pascal_case = "".join(part.capitalize() for part in memory_type.value.split("_"))
                return ["Memory", pascal_case]
        
        # Fallback to class name
        return ["Memory", cls.__name__]
    
    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j-compatible property dict."""
        props = self.model_dump()
        
        # Convert UUID to string for Neo4j
        if isinstance(props.get('id'), UUID):
            props['id'] = str(props['id'])
        
        # Convert datetime to timestamp
        if isinstance(props.get('timestamp'), datetime):
            props['timestamp'] = props['timestamp'].timestamp()
        
        # Convert enum to value
        if isinstance(props.get('memory_type'), MemoryType):
            props['memory_type'] = props['memory_type'].value
            
        # Convert other UUIDs to strings
        for key, value in props.items():
            if isinstance(value, UUID):
                props[key] = str(value)
        
        return props
    
    @classmethod
    def from_neo4j_record(cls, record: dict):
        """Create instance from Neo4j record."""
        # Convert string back to UUID
        if 'id' in record and isinstance(record['id'], str):
            record['id'] = UUID(record['id'])
        
        # Convert timestamp back to datetime
        if 'timestamp' in record and isinstance(record['timestamp'], int | float):
            record['timestamp'] = datetime.fromtimestamp(record['timestamp'])
        
        # Convert memory_type string back to enum
        if 'memory_type' in record and isinstance(record['memory_type'], str):
            record['memory_type'] = MemoryType(record['memory_type'])
        
        # Convert other string UUIDs back to UUID objects
        for key, value in record.items():
            if key.endswith('_id') and isinstance(value, str):
                with contextlib.suppress(ValueError):
                    record[key] = UUID(value)
        
        return cls(**record)
