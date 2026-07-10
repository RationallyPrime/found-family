"""Error handlers for different types of errors"""

from fastapi import status
from starlette.exceptions import HTTPException

from memory_palace.core.logging import get_logger

from .base import ApplicationError, ErrorCode, ErrorLevel
from .error_context import ErrorContext, ErrorContextManager

logger = get_logger(__name__)


class ErrorHandler:
    """Base class for error handlers"""

    def __init__(
        self,
        context_manager: ErrorContextManager,
    ) -> None:
        self.context_manager = context_manager

    def _format_response(
        self,
        error_context: ErrorContext,
        level: ErrorLevel,
        additional_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Format error response"""
        # Start with basic error information
        response = {
            "error": str(error_context.error),
            "error_code": (additional_context or {}).get("error_code", ErrorCode.PROCESSING_FAILED),
            "level": level.value,
            "trace_id": error_context.trace_id,
            "timestamp": error_context.timestamp,
        }

        # Include rich structured data if it's an ApplicationError
        if isinstance(error_context.error, ApplicationError):
            response["error_code"] = error_context.error.code.value
            # Handle different types of details
            if hasattr(error_context.error.details, "model_dump"):
                details_dict = error_context.error.details.model_dump()
            elif isinstance(error_context.error.details, dict):
                details_dict = error_context.error.details
            else:
                details_dict = None
            if details_dict:
                response["details"] = details_dict

        # Add suggested solution if available
        if additional_context and additional_context.get("suggested_solution"):
            response["suggested_solution"] = additional_context["suggested_solution"]

        return response

    async def handle_async(
        self,
        error: Exception,  # noqa: ARG002
        level: ErrorLevel,
        context: dict[str, object],
    ) -> dict[str, object]:
        """Handle error asynchronously"""
        async with self.context_manager as error_context:
            return self._format_response(error_context, level, context)

    def handle_sync(
        self,
        error: Exception,  # noqa: ARG002
        level: ErrorLevel,
        context: dict[str, object],
    ) -> dict[str, object]:
        """Handle error synchronously"""
        with self.context_manager as error_context:
            return self._format_response(error_context, level, context)


class GlobalErrorHandler(ErrorHandler):
    """Global error handler for FastAPI application"""

    async def handle_http_exception(self, error: HTTPException) -> dict[str, object]:
        """Handle HTTP exceptions"""
        level = ErrorLevel.ERROR if error.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR else ErrorLevel.WARNING
        error_context = await self.context_manager.capture_context(error, status_code=error.status_code)
        return self._format_response(error_context=error_context, level=level)
