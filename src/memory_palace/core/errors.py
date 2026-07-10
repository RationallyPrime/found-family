"""Specific error types for the Memory Palace application."""

from collections.abc import Mapping

from pydantic import JsonValue

from .base import ApplicationError, ErrorCode, ErrorDetails, ErrorLevel, ServiceErrorDetails

StructuredDetails = ErrorDetails | Mapping[str, JsonValue]


class ServiceError(ApplicationError):
    """Error from external service calls."""

    def __init__(self, message: str, details: ServiceErrorDetails | None = None) -> None:
        super().__init__(
            message=message,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            level=ErrorLevel.ERROR,
            details=details or ServiceErrorDetails(source="service", operation="external_call", service_name="unknown"),
        )


class AuthenticationError(ApplicationError):
    """Authentication-related errors."""

    def __init__(self, message: str, details: StructuredDetails | None = None) -> None:
        super().__init__(message=message, code=ErrorCode.AUTHENTICATION_FAILED, level=ErrorLevel.ERROR, details=details)


class ProcessingError(ApplicationError):
    """General processing errors."""

    def __init__(self, message: str, details: StructuredDetails | None = None) -> None:
        super().__init__(message=message, code=ErrorCode.PROCESSING_FAILED, level=ErrorLevel.ERROR, details=details)


class RateLimitError(ApplicationError):
    """Rate limiting errors."""

    def __init__(self, message: str, details: ServiceErrorDetails | None = None) -> None:
        super().__init__(message=message, code=ErrorCode.RATE_LIMITED, level=ErrorLevel.WARNING, details=details)


class TimeoutError(ApplicationError):
    """Timeout errors."""

    def __init__(self, message: str, details: StructuredDetails | None = None) -> None:
        super().__init__(message=message, code=ErrorCode.TIMEOUT, level=ErrorLevel.ERROR, details=details)
