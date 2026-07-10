"""Base error classes and enums"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer


class ErrorLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    def to_logging_level(self) -> int:
        """Convert ErrorLevel to logging level"""
        return {
            ErrorLevel.DEBUG: logging.DEBUG,
            ErrorLevel.INFO: logging.INFO,
            ErrorLevel.WARNING: logging.WARNING,
            ErrorLevel.ERROR: logging.ERROR,
            ErrorLevel.CRITICAL: logging.CRITICAL,
        }[self]


class ErrorCode(StrEnum):
    """Error codes for the application."""

    # General Errors (1xxx)
    UNKNOWN = "1000"
    INVALID_REQUEST = "1001"
    INVALID_INPUT = "1002"
    NOT_FOUND = "1003"
    PROCESSING_FAILED = "1004"
    CONFIG_INVALID = "1005"
    CONFIG_MISSING = "1006"
    TIMEOUT = "1007"
    INVALID_EMAIL = "1008"
    INVALID_EMAIL_DOMAIN = "1009"
    USER_ALREADY_EXISTS = "1010"
    USER_CREATION_FAILED = "1011"
    USER_NOT_FOUND = "1012"
    INVALID_TOKEN = "1013"  # noqa: S105 - public error code, not a credential

    # API Errors (2xxx)
    AUTHENTICATION_FAILED = "2001"
    AUTHORIZATION_FAILED = "2002"
    RATE_LIMITED = "2003"  # Unified rate limit error code
    CONTENT_NOT_FOUND = "2004"
    CIRCUIT_OPEN = "2005"

    # Database Errors (3xxx)
    DB_CONNECTION = "3001"
    DB_QUERY = "3002"
    DB_VALIDATION = "3003"
    DB_RECORD_NOT_FOUND = "3004"
    DB_OPERATION = "3005"

    # AI/ML Errors (4xxx)
    MODEL_ERROR = "4001"
    MODEL_INITIALIZATION_ERROR = "4002"
    EMBEDDING_FAILED = "4003"

    # Infrastructure Errors (5xxx)
    RESOURCE_ERROR = "5001"
    SERVICE_UNAVAILABLE = "5002"
    QUEUE_ERROR = "5003"

    # Storage Errors (6xxx)
    STORAGE_ERROR = "6001"
    STORAGE_CONNECTION = "6002"
    STORAGE_OPERATION = "6003"


class ErrorDetails(BaseModel):
    """Base model for structured error details"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(description="Component or module where the error occurred")
    operation: str = Field(description="Operation being performed when the error occurred")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When the error occurred")
    metadata: dict[str, JsonValue] = Field(default_factory=dict, description="Operation-specific structured context")

    # Ensure timestamp is serialized consistently
    @field_serializer("timestamp")
    def serialize_timestamp(self, timestamp: datetime) -> str:
        return timestamp.isoformat()


class ValidationErrorDetails(ErrorDetails):
    """Details for validation-related errors"""

    field: str | None = Field(None, description="Field that failed validation")
    actual_value: object = Field(None, description="Value that failed validation")
    expected_type: str | None = Field(None, description="Expected type or format")
    constraint: str | None = Field(None, description="Constraint that was violated")


class ResourceErrorDetails(ErrorDetails):
    """Details for resource-related errors"""

    resource_id: str | None = Field(None, description="ID of the resource")
    resource_type: str = Field(description="Type of resource (document, user, etc.)")
    action: str = Field(description="Action attempted (read, write, delete, etc.)")


class ServiceErrorDetails(ErrorDetails):
    """Details for service-related errors"""

    service_name: str = Field(description="Name of the service that failed")
    endpoint: str | None = Field(None, description="Service endpoint that was called")
    status_code: int | None = Field(None, description="HTTP or service status code")
    request_id: str | None = Field(None, description="Request ID for tracing")
    latency_ms: float | None = Field(None, description="Response time in milliseconds")


class StorageErrorDetails(ServiceErrorDetails):
    """Details for storage-related errors"""

    bucket: str | None = Field(None, description="Storage bucket name")
    object_path: str | None = Field(None, description="Path to the object in storage")


class DatabaseErrorDetails(ServiceErrorDetails):
    """Details for database-related errors"""

    query_type: str | None = Field(None, description="Type of query (select, insert, etc.)")
    table: str | None = Field(None, description="Database table name")
    transaction_id: str | None = Field(None, description="Database transaction ID")


class AIServiceErrorDetails(ServiceErrorDetails):
    """Details for AI service-related errors"""

    model_name: str | None = Field(None, description="AI model name")
    prompt_tokens: int | None = Field(None, description="Number of tokens in the prompt")
    max_tokens: int | None = Field(None, description="Maximum tokens allowed")
    temperature: float | None = Field(None, description="Temperature setting used")


class ApplicationError(Exception):
    """Base class for all application errors"""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        level: ErrorLevel = ErrorLevel.ERROR,
        details: ErrorDetails | Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.level = level

        # Convert dict to ErrorDetails if needed
        if details is None:
            self.details = ErrorDetails(source="unknown", operation="unknown")
        elif isinstance(details, Mapping):
            metadata = dict(details)
            source_value = metadata.pop("source", "unknown")
            operation_value = metadata.pop("operation", "unknown")
            self.details = ErrorDetails(
                source=str(source_value),
                operation=str(operation_value),
                metadata=metadata,
            )
        else:
            self.details = details

        super().__init__(message)


class ErrorMetadata(BaseModel):
    """Metadata for error tracking and analysis"""

    code: ErrorCode = Field(description="Error code identifying the type of error")
    level: ErrorLevel = Field(description="Severity level of the error")
    timestamp: datetime = Field(description="When the error occurred")
    trace_id: UUID = Field(description="Unique identifier for tracing this error")
    service: str = Field(description="Service where the error occurred")
    endpoint: str | None = Field(None, description="API endpoint if applicable")
    user_id: UUID | None = Field(None, description="User ID if authenticated")
    additional_data: dict[str, object] = Field(default_factory=dict, description="Extra context")

    # Ensure timestamp is serialized consistently
    @field_serializer("timestamp")
    def serialize_timestamp(self, timestamp: datetime) -> str:
        return timestamp.isoformat()
