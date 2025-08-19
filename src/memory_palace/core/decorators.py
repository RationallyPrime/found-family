"""Error handling decorators"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar, cast

from .base import ApplicationError, ErrorLevel
from .error_context import ErrorContextManager
from .handlers import ErrorHandler
from .logging import get_logger

logger = get_logger(__name__)
P = ParamSpec("P")
T = TypeVar("T")


class ErrorHandlerProtocol(Protocol):
    """Protocol for error handlers"""

    async def handle_async(
        self,
        error: Exception,
        level: ErrorLevel,
        context: dict[str, Any],
    ) -> None: ...

    def handle_sync(
        self,
        error: Exception,
        level: ErrorLevel,
        context: dict[str, Any],
    ) -> None: ...


def with_error_handling(
    error_level: ErrorLevel = ErrorLevel.ERROR,
    reraise: bool = True,
    error_handler: ErrorHandler | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator for handling errors in functions.

    Args:
        error_level: Severity level for error logging
        reraise: Whether to re-raise the error after handling
        error_handler: Optional custom error handler

    Returns:
        Decorated function with error handling
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:  # ty:ignore
        # Capture the original signature to preserve it
        original_signature = inspect.signature(func)

        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return await cast("Callable[P, Awaitable[T]]", func)(*args, **kwargs)  # ty:ignore
                except ApplicationError as e:
                    async with ErrorContextManager(e) as ctx:
                        error_context: dict[str, Any] = {
                            "function": func.__name__,  # ty: ignore
                            "error_context": ctx.to_dict(),
                        }
                        if error_handler:
                            await error_handler.handle_async(
                                error=e,
                                level=e.level,
                                context=error_context,
                            )
                        else:
                            logger.log(
                                e.level.to_logging_level(),
                                f"Error in {func.__name__}: {e!s}",  # ty:ignore
                                extra=error_context,
                                exc_info=True,
                            )
                        if reraise:
                            raise
                        return cast("T", None)
                except Exception as e:
                    async with ErrorContextManager(e) as ctx:
                        error_context = {
                            "function": func.__name__,  # ty:ignore
                            "error_context": ctx.to_dict(),
                        }
                        if error_handler:
                            await error_handler.handle_async(
                                e,
                                error_level,
                                error_context,
                            )
                        else:
                            logger.log(
                                error_level.to_logging_level(),
                                f"Error in {func.__name__}: {e!s}",  # ty:ignore
                                extra=error_context,
                                exc_info=True,
                            )
                        if reraise:
                            raise
                        return cast("T", None)

            # Explicitly set the signature on the wrapper to match the original function
            async_wrapper.__signature__ = original_signature  # type: ignore
            return cast("Callable[P, T]", async_wrapper)  # ty:ignore
        else:

            @wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return func(*args, **kwargs)
                except ApplicationError as e:
                    with ErrorContextManager(e) as ctx:
                        error_context: dict[str, Any] = {
                            "function": func.__name__,  # ty:ignore
                            "error_context": ctx.to_dict(),
                        }
                        if error_handler:
                            error_handler.handle_sync(
                                e,
                                e.level,
                                context=error_context,
                            )
                        else:
                            logger.log(
                                e.level.to_logging_level(),
                                f"Error in {func.__name__}: {e!s}",  # ty:ignore
                                extra=error_context,
                                exc_info=True,
                            )
                        if reraise:
                            raise
                        return cast("T", None)
                except Exception as e:
                    with ErrorContextManager(e) as ctx:
                        error_context = {
                            "function": func.__name__,  # ty:ignore
                            "error_context": ctx.to_dict(),
                        }
                        if error_handler:
                            error_handler.handle_sync(
                                e,
                                error_level,
                                error_context,
                            )
                        else:
                            logger.log(
                                error_level.to_logging_level(),
                                f"Error in {func.__name__}: {e!s}",  # ty:ignore
                                extra=error_context,
                                exc_info=True,
                            )
                        if reraise:
                            raise
                        return cast("T", None)

            # Explicitly set the signature on the wrapper to match the original function
            sync_wrapper.__signature__ = original_signature  # type: ignore
            return cast("Callable[P, T]", sync_wrapper)  # ty:ignore

    return decorator


def error_context(
    error_level: ErrorLevel = ErrorLevel.ERROR,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator for adding error context."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:  # ty:ignore
        # Capture the original signature to preserve it
        original_signature = inspect.signature(func)

        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return await cast("Callable[P, Awaitable[T]]", func)(*args, **kwargs)  # ty:ignore
                except Exception as e:
                    async with ErrorContextManager(e):
                        logger.log(
                            error_level.to_logging_level(),
                            f"Error context for {func.__name__}: {e!s}",  # ty:ignore
                            exc_info=True,
                        )
                    raise

            # Explicitly set the signature on the wrapper to match the original function
            async_wrapper.__signature__ = original_signature  # type: ignore
            return cast("Callable[P, T]", async_wrapper)  # ty:ignore
        else:

            @wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    with ErrorContextManager(e):
                        logger.log(
                            error_level.to_logging_level(),
                            f"Error context for {func.__name__}: {e!s}",  # ty:ignore
                            exc_info=True,
                        )
                    raise

            # Explicitly set the signature on the wrapper to match the original function
            sync_wrapper.__signature__ = original_signature  # type: ignore
            return sync_wrapper

    return decorator


def handle_error(
    raise_original: bool = True,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator for handling errors with option to raise original."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:  # ty:ignore
        # Capture the original signature to preserve it
        original_signature = inspect.signature(func)

        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return await cast("Callable[P, Awaitable[T]]", func)(*args, **kwargs)  # ty:ignore
                except Exception as e:
                    async with ErrorContextManager(e):
                        if raise_original:
                            raise
                        logger.error(f"Handled error in {func.__name__}: {e!s}")  # ty:ignore
                        raise RuntimeError(f"Error in {func.__name__}")  # ty:ignore  # noqa: B904

            # Explicitly set the signature on the wrapper to match the original function
            async_wrapper.__signature__ = original_signature  # type: ignore
            return cast("Callable[P, T]", async_wrapper)  # ty:ignore
        else:

            @wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    with ErrorContextManager(e):
                        if raise_original:
                            raise
                        logger.error(f"Handled error in {func.__name__}: {e!s}")  # ty:ignore
                        raise RuntimeError(f"Error in {func.__name__}")  # ty:ignore  # noqa: B904

            # Explicitly set the signature on the wrapper to match the original function
            sync_wrapper.__signature__ = original_signature  # type: ignore
            return sync_wrapper

    return decorator
