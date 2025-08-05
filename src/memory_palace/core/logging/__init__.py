"""Structured logging module.

This module provides utilities for structured logging using logfire.
"""

# Import from base module (for backward compatibility)
from .context import (
    clear_log_context,
    critical,
    debug,
    error,
    get_log_context,
    info,
    set_log_context,
    update_log_context,
    warning,
)
from .setup import get_logger, setup_logging

__all__ = [
    "clear_log_context",
    "critical",
    # Log levels
    "debug",
    "error",
    # Context management
    "get_log_context",
    # Setup
    "get_logger",
    "info",
    # Instrumentation
    "set_log_context",
    "setup_logging",
    "update_log_context",
    "warning",
]
