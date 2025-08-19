"""Utility functions for domain models."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Get the current UTC datetime with timezone awareness."""
    return datetime.now(UTC)
