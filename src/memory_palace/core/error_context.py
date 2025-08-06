"""Error context management"""

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .base import ApplicationError

logger = logging.getLogger(__name__)


class ErrorContext:
    """Captures and stores context around an error"""

    def __init__(self, error: Exception, trace_id: str | None = None, **context: Any):
        self.error = error
        self.trace_id = trace_id or str(uuid4())
        self.timestamp = datetime.now(UTC)
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary format with structured details from ApplicationError"""
        result = {
            "error_type": self.error.__class__.__name__,
            "error_message": str(self.error),
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
        }

        # Extract structured details from ApplicationError
        if isinstance(self.error, ApplicationError):
            result["error_code"] = self.error.code.value
            result["error_level"] = self.error.level.value

            # Convert Pydantic ErrorDetails to dict and flatten into the context
            details_dict = self.error.details.model_dump()
            # Prefix the details fields to avoid collisions
            for key, value in details_dict.items():
                result[f"details.{key}"] = value

        # Add any additional context
        if self.context:
            # Prefix each context item to avoid key collisions
            for key, value in self.context.items():
                result[f"context.{key}"] = value

        return result


class ErrorContextManager:
    """Manages error contexts across the application"""

    def __init__(self, error: Exception | None = None, **context: Any) -> None:
        self._contexts: dict[str, ErrorContext] = {}
        self._error = error
        self._context = context
        self._current_context: ErrorContext | None = None

    async def __aenter__(self) -> ErrorContext:
        """Enter async context, capturing error context"""
        if self._error is None:
            raise ValueError("No error provided for context")
        self._current_context = ErrorContext(self._error, **self._context)
        self._contexts[self._current_context.trace_id] = self._current_context
        return self._current_context

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context"""
        # If there's a new exception during context handling, log it
        if exc_type is not None and exc_val is not None:
            logger.error(
                f"Exception during error context handling: {exc_type.__name__}: {exc_val}",
                exc_info=(exc_type, exc_val, exc_tb),
            )

    def __enter__(self) -> ErrorContext:
        """Enter sync context, capturing error context"""
        if self._error is None:
            raise ValueError("No error provided for context")
        self._current_context = ErrorContext(self._error, **self._context)
        self._contexts[self._current_context.trace_id] = self._current_context
        return self._current_context

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit sync context"""
        # If there's a new exception during context handling, log it
        if exc_type is not None and exc_val is not None:
            logger.error(
                f"Exception during error context handling: {exc_type.__name__}: {exc_val}",
                exc_info=(exc_type, exc_val, exc_tb),
            )

    async def capture_context(self, error: Exception, **context: Any) -> ErrorContext:
        """Capture error context with additional data"""
        error_context = ErrorContext(error, **context)
        self._contexts[error_context.trace_id] = error_context
        return error_context

    def get_context(self, trace_id: str) -> ErrorContext | None:
        """Retrieve error context by trace ID"""
        return self._contexts.get(trace_id)

    @staticmethod
    def extract_details_from_model(model: BaseModel, prefix: str = "") -> dict[str, Any]:
        """Extract fields from a Pydantic model for structured logging.

        Args:
            model: Pydantic model to extract fields from
            prefix: Optional prefix for field names

        Returns:
            Dictionary of model fields with optional prefix
        """
        result: dict[str, Any] = {}

        # Get model data as dict
        model_data = model.model_dump()

        # Add each field with prefix
        for key, value in model_data.items():
            # Skip nested objects but include primitive types
            if not isinstance(value, dict | list | set | tuple):
                field_name = f"{prefix}.{key}" if prefix else key
                result[field_name] = value

        return result
