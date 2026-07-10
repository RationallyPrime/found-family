"""Circuit breaker implementation for handling failures and retries."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from threading import Lock
from typing import ParamSpec, TypeVar

from memory_palace.core.base import ServiceErrorDetails
from memory_palace.core.errors import RateLimitError, ServiceError, TimeoutError
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")
P = ParamSpec("P")


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker[T]:
    """
    Circuit breaker for handling service failures gracefully.

    The circuit breaker has three states:
    - CLOSED: Normal operation, calls go through
    - OPEN: Service is failing, calls are rejected immediately
    - HALF_OPEN: Testing if service has recovered

    When the failure threshold is exceeded, the circuit opens.
    After a timeout, it transitions to half-open to test recovery.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception_types: tuple[type[Exception], ...] = (Exception,),
        success_threshold: int = 2,
    ) -> None:
        """
        Initialize circuit breaker.

        Args:
            name: Name of the circuit (for logging)
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying half-open
            expected_exception_types: Exceptions that trigger the breaker
            success_threshold: Successes needed in half-open to close circuit
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception_types = expected_exception_types
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float | None = None
        self._last_failure_monotonic: float | None = None
        self.last_exception: Exception | None = None
        self._state_lock = Lock()
        self._half_open_probe_in_flight = False

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._last_failure_monotonic is None:
            return False
        return time.monotonic() - self._last_failure_monotonic >= self.recovery_timeout

    def _record_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                logger.info(f"Circuit breaker '{self.name}' closing after recovery")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                self.last_exception = None
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success in closed state
            self.failure_count = 0

    def _record_failure(self, exception: Exception) -> None:
        """Record a failed call."""
        self.last_failure_time = time.time()
        self._last_failure_monotonic = time.monotonic()
        self.last_exception = exception

        if self.state == CircuitState.HALF_OPEN:
            # Failed in half-open, go back to open
            logger.warning(f"Circuit breaker '{self.name}' reopening after half-open failure")
            self.state = CircuitState.OPEN
            self.failure_count = 1
            self.success_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                logger.error(
                    f"Circuit breaker '{self.name}' opening after {self.failure_count} failures",
                    last_exception=str(exception),
                )
                self.state = CircuitState.OPEN

    def _check_state(self) -> None:
        """Check and potentially update circuit state."""
        if self.state == CircuitState.OPEN and self._should_attempt_reset():
            logger.info(f"Circuit breaker '{self.name}' attempting reset (half-open)")
            self.state = CircuitState.HALF_OPEN
            self.success_count = 0

    def _begin_call(self, operation: str) -> bool:
        """Atomically reserve the single half-open probe slot."""
        with self._state_lock:
            self._check_state()
            if self.state == CircuitState.OPEN or (
                self.state == CircuitState.HALF_OPEN and self._half_open_probe_in_flight
            ):
                raise self._open_error(operation)
            if self.state == CircuitState.HALF_OPEN:
                self._half_open_probe_in_flight = True
                return True
            return False

    def _release_probe(self, is_half_open_probe: bool) -> None:
        if is_half_open_probe:
            self._half_open_probe_in_flight = False

    async def call_async(
        self,
        func: Callable[P, Awaitable[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """
        Call an async function through the circuit breaker.

        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from the function

        Raises:
            ServiceError: If circuit is open
            Original exception: If function fails and circuit allows
        """
        is_half_open_probe = self._begin_call("call_async")

        try:
            result = await func(*args, **kwargs)
        except self.expected_exception_types as exc:
            with self._state_lock:
                self._release_probe(is_half_open_probe)
                self._record_failure(exc)
            raise
        except BaseException:
            if is_half_open_probe:
                with self._state_lock:
                    self._release_probe(is_half_open_probe)
            raise

        with self._state_lock:
            self._release_probe(is_half_open_probe)
            self._record_success()
        return result

    def call_sync(
        self,
        func: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """
        Call a sync function through the circuit breaker.

        Args:
            func: Sync function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from the function

        Raises:
            ServiceError: If circuit is open
            Original exception: If function fails and circuit allows
        """
        is_half_open_probe = self._begin_call("call_sync")

        try:
            result = func(*args, **kwargs)
        except self.expected_exception_types as exc:
            with self._state_lock:
                self._release_probe(is_half_open_probe)
                self._record_failure(exc)
            raise
        except BaseException:
            if is_half_open_probe:
                with self._state_lock:
                    self._release_probe(is_half_open_probe)
            raise
        with self._state_lock:
            self._release_probe(is_half_open_probe)
            self._record_success()
        return result

    def _open_error(self, operation: str) -> ServiceError:
        """Build the uniform fast-fail error for unavailable circuits."""
        return ServiceError(
            message=f"Circuit breaker '{self.name}' is open",
            details=ServiceErrorDetails(
                source="circuit_breaker",
                operation=operation,
                service_name=self.name,
                endpoint=None,
                status_code=503,
                request_id=None,
                latency_ms=None,
            ),
        )

    def get_state(self) -> dict[str, object]:
        """Get current circuit breaker state for monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_exception": str(self.last_exception) if self.last_exception else None,
        }


class RetryWithCircuitBreaker[T]:
    """
    Combines retry logic with circuit breaker pattern.

    This handles both transient failures (with retries) and
    persistent failures (with circuit breaking).
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker[T],
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        retryable_exceptions: tuple[type[Exception], ...] = (
            RateLimitError,
            TimeoutError,
        ),
    ) -> None:
        """
        Initialize retry with circuit breaker.

        Args:
            circuit_breaker: Circuit breaker to use
            max_retries: Maximum number of retries
            initial_delay: Initial delay between retries in seconds
            backoff_factor: Multiplier for delay between retries
            max_delay: Maximum delay between retries
            retryable_exceptions: Exceptions that should trigger retry
        """
        self.circuit_breaker = circuit_breaker
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions

    async def call_async(
        self,
        func: Callable[P, Awaitable[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """
        Call an async function with retries and circuit breaker.

        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from the function

        Raises:
            Last exception encountered after all retries
        """
        last_exception: Exception | None = None
        delay = self.initial_delay

        for attempt in range(1, self.max_retries + 1):
            # Attempt the call; retry only the configured exception types.
            # This try/except IS the retry mechanism — the one place the
            # no-try-except rule doesn't apply, because this module is the
            # error-handling infrastructure itself.
            try:
                return await self.circuit_breaker.call_async(func, *args, **kwargs)
            except self.retryable_exceptions as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(
                        f"Retryable failure on '{self.circuit_breaker.name}' "
                        f"(attempt {attempt}/{self.max_retries}), retrying in {delay:.1f}s",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * self.backoff_factor, self.max_delay)

        # All retries exhausted
        if last_exception:
            raise last_exception

        raise ServiceError(
            message=f"All {self.max_retries} retries exhausted",
            details=ServiceErrorDetails(
                source="retry_circuit_breaker",
                operation="call_async",
                service_name=self.circuit_breaker.name,
                endpoint=None,
                status_code=503,
                request_id=None,
                latency_ms=None,
            ),
        )
