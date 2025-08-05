"""Base logging functionality.

This module contains base logging functionality used by other logging modules.
"""

import structlog
from structlog.typing import FilteringBoundLogger


# Default log level
LOG_LEVEL = "INFO"


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Get a configured logger instance.

    Args:
        name: The name of the logger to get.

    Returns:
        FilteringBoundLogger: A configured structlog logger instance.
    """
    # Return a structlog logger
    # If structlog is not configured yet, it will use a default configuration
    return structlog.get_logger(name)
