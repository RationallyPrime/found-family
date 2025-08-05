"""Centralized logging setup with Logfire integration.

This module provides simplified logging functions for the application.
Logfire is configured via environment variables and pyproject.toml.
"""

import logging
import sys

import logfire
import structlog
from structlog.processors import CallsiteParameter, CallsiteParameterAdder
from structlog.types import EventDict, Processor, WrappedLogger
from structlog.typing import FilteringBoundLogger


def add_logfire_context(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Add Logfire-specific context to log events.

    Args:
        logger: The wrapped logger instance
        method_name: The name of the logging method
        event_dict: The event dictionary

    Returns:
        The event dictionary with added context
    """
    # Add any custom fields that should be tracked by Logfire
    # These will be available as attributes in Logfire
    if "error" in event_dict:
        event_dict["error_type"] = type(event_dict["error"]).__name__

    return event_dict


def setup_logging() -> None:
    """Set up application-wide logging with Logfire and structlog integration.

    Logfire is primarily configured via environment variables:
    - LOGFIRE_TOKEN: Authentication token
    - LOGFIRE_SERVICE_NAME: Service name (defaults to project name)
    - LOGFIRE_ENVIRONMENT: Environment (defaults to "development")

    This function configures structlog to work seamlessly with Logfire.
    """
    # Common processors for structured logging
    processors: list[Processor] = [
        # Merge context from contextvars
        structlog.contextvars.merge_contextvars,
        # Add log level to event dict
        structlog.processors.add_log_level,
        # Add timestamp in ISO format
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Add callsite parameters (file, line, function)
        CallsiteParameterAdder(
            parameters=[
                CallsiteParameter.FILENAME,
                CallsiteParameter.LINENO,
                CallsiteParameter.FUNC_NAME,
            ]
        ),
        # Add custom Logfire context
        add_logfire_context,
        # Stack trace formatting
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        # Add the Logfire processor for structured logging (MUST come before final renderer)
        logfire.StructlogProcessor(),
        # Final console renderer
        structlog.dev.ConsoleRenderer(colors=True),
    ]

    # Configure structlog
    structlog.configure(
        processors=processors,
        # Use PrintLogger to avoid double logging with standard library
        logger_factory=structlog.PrintLoggerFactory(),
        # Cache logger instances
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to format logs consistently
    # This ensures that logs from libraries using standard logging
    # are also properly formatted
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    # Add structlog's ProcessorFormatter to standard logging
    # This makes standard library logs also go through structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=processors[:-2],  # Exclude the Logfire processor and final renderer
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Apply to root logger
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)


def instrument_libraries() -> None:
    """Instrument various libraries for automatic Logfire logging.

    This function instruments common libraries used in the application.
    Called automatically by Logfire based on pyproject.toml configuration.
    """
    # Auto-instrumentation is configured in pyproject.toml
    # Manual instrumentation is handled in main.py where needed
    pass


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Get a structured logger instance that's properly configured with Logfire.

    Args:
        name: The name of the logger (usually __name__)

    Returns:
        A configured structlog logger instance
    """
    return structlog.get_logger(name)
