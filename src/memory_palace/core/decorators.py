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


def with_session(driver_attr: str = "driver") -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator to automatically manage Neo4j session lifecycle.

    Eliminates duplicated session management code by automatically wrapping
    methods with async session context management.

    Args:
        driver_attr: Name of the attribute containing the AsyncDriver (default: "driver")

    Usage:
        @with_session()
        async def my_method(self, session, other_args):
            # session is automatically injected as first parameter after self
            result = await session.run(query)

    Example:
        Before:
            async def refresh_salience(self):
                async with self.driver.session() as session:
                    result = await session.run(query, params)

        After:
            @with_session()
            async def refresh_salience(self, session):
                result = await session.run(query, params)
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:  # ty:ignore
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Extract self (first argument)
            if not args:
                raise ValueError(f"{func.__name__} requires at least 'self' argument")

            self_obj = args[0]
            driver = getattr(self_obj, driver_attr, None)

            if driver is None:
                raise AttributeError(
                    f"Object {self_obj.__class__.__name__} has no attribute '{driver_attr}'. "
                    f"Either provide the correct driver_attr or ensure the object has a driver."
                )

            # Create session and inject as second argument (after self)
            async with driver.session() as session:
                new_args = (args[0], session) + args[1:]
                return await func(*new_args, **kwargs)  # ty: ignore

        return cast(Callable[P, T], wrapper)

    return decorator
