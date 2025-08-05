"""Logging context utilities for structured logging.

This module provides utilities for working with logging context in a structured way,
particularly useful when working with logfire and OpenTelemetry.
"""

from contextvars import ContextVar
from typing import Any

# Import get_logger function from base module
from .base import get_logger

# Create a logger
logger = get_logger(__name__)

# Context variable to store request-scoped logging context
# Use None as default to avoid mutable default value issues
_log_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)


def get_log_context() -> dict[str, Any]:
    """Get the current logging context.

    Returns:
        Dict containing the current logging context
    """
    context: dict[str, Any] | None = _log_context.get()
    if context is None:
        # Initialize with empty dict on first access
        context = {}
        _log_context.set(context)
    return context.copy()


def set_log_context(context: dict[str, Any]) -> None:
    """Set the logging context.

    Args:
        context: Dictionary with logging context data
    """
    _log_context.set(context)


def update_log_context(key: str, value: Any) -> None:
    """Update a single key in the logging context.

    Args:
        key: Context key to update
        value: Value to set
    """
    context = get_log_context()
    context[key] = value
    _log_context.set(context)


def clear_log_context() -> None:
    """Clear the current logging context."""
    _log_context.set({})


def log_with_context(
    level: str,
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log a message with the current context.

    Args:
        level: Log level (debug, info, warning, error, critical)
        message: Log message
        extra: Additional context to include
        logger_name: Optional alternative logger name
    """
    logger_instance = logger
    if logger_name:
        logger_instance = get_logger(logger_name)

    # Combine context and extra
    context = get_log_context()
    if extra:
        context.update(extra)

    # With structlog, we bind the context and then log
    # This provides better structured logging support
    bound_logger = logger_instance.bind(**context)
    log_method = getattr(bound_logger, level.lower())
    log_method(message)


# Convenience methods
def debug(
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log a debug message with context."""
    log_with_context("debug", message, extra, logger_name)


def info(
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log an info message with context."""
    log_with_context("info", message, extra, logger_name)


def warning(
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log a warning message with context."""
    log_with_context("warning", message, extra, logger_name)


def error(
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log an error message with context."""
    log_with_context("error", message, extra, logger_name)


def critical(
    message: str,
    extra: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> None:
    """Log a critical message with context."""
    log_with_context("critical", message, extra, logger_name)
