"""Analysis models adapted from Automining for Memory Palace."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from memory_palace.domain.models.utils import utc_now


class AnalysisType(str, Enum):
    """Types of analysis."""

    SENTIMENT = "sentiment"
    SUMMARIZATION = "summarization"
    ENTITY_EXTRACTION = "entity_extraction"
    TOPIC_MODELING = "topic_modeling"
    QUALITY_ASSESSMENT = "quality_assessment"
    SALIENCE_SCORING = "salience_scoring"


class SentimentScore(BaseModel):
    """Sentiment analysis scores."""

    positive: float = Field(ge=0, le=1)
    negative: float = Field(ge=0, le=1)
    neutral: float = Field(ge=0, le=1)
    compound: float = Field(ge=-1, le=1)  # Overall sentiment


class Entity(BaseModel):
    """Extracted entity."""

    text: str
    type: str  # PERSON, ORGANIZATION, LOCATION, CONCEPT, etc.
    confidence: float = Field(ge=0, le=1)
    occurrences: int = 1


class Topic(BaseModel):
    """Topic from topic modeling."""

    id: int
    name: str
    keywords: list[str]
    weight: float = Field(ge=0, le=1)
    description: str | None = None


class QualityMetrics(BaseModel):
    """Conversation quality metrics."""

    coherence_score: float = Field(ge=0, le=1)
    relevance_score: float = Field(ge=0, le=1)
    depth_score: float = Field(ge=0, le=1)
    clarity_score: float = Field(ge=0, le=1)

    def overall_score(self) -> float:
        """Calculate overall quality score."""
        scores = [
            self.coherence_score,
            self.relevance_score,
            self.depth_score,
            self.clarity_score,
        ]
        return sum(scores) / len(scores)


class MemoryAnalysis(BaseModel):
    """Analysis result for a memory/conversation."""

    id: UUID = Field(default_factory=uuid4)
    memory_id: UUID  # ID of the memory/conversation analyzed
    analysis_type: AnalysisType
    timestamp: datetime = Field(default_factory=utc_now)

    # Analysis results (populated based on type)
    sentiment: SentimentScore | None = None
    summary: str | None = None
    entities: list[Entity] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    quality_metrics: QualityMetrics | None = None
    salience_score: float | None = Field(None, ge=0, le=1)

    # Metadata
    model_used: str | None = None
    processing_time_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
