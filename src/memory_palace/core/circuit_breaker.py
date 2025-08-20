"""Circuit breaker implementation for handling failures and retries."""

import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, Generic, TypeVar

from memory_palace.core.base import ServiceErrorDetails
from memory_palace.core.errors import RateLimitError, ServiceError, TimeoutError
from memory_palace.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker(Generic[T]):
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
    ):
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
        self.last_exception: Exception | None = None

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return False
        return time.time() - self.last_failure_time >= self.recovery_timeout

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

    async def call_async(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
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
        self._check_state()

        if self.state == CircuitState.OPEN:
            error_msg = f"Circuit breaker '{self.name}' is open"
            if self.last_exception:
                error_msg += f" (last error: {self.last_exception})"

            raise ServiceError(
                message=error_msg,
                details=ServiceErrorDetails(
                    source="circuit_breaker",
                    operation="call_async",
                    service_name=self.name,
                    endpoint=None,
                    status_code=503,  # Service Unavailable
                    request_id=None,
                    latency_ms=None,
                ),
            )

        # Try to execute the function
        result = await func(*args, **kwargs)
        self._record_success()
        return result

    def call_sync(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
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
        self._check_state()

        if self.state == CircuitState.OPEN:
            error_msg = f"Circuit breaker '{self.name}' is open"
            if self.last_exception:
                error_msg += f" (last error: {self.last_exception})"

            raise ServiceError(
                message=error_msg,
                details=ServiceErrorDetails(
                    source="circuit_breaker",
                    operation="call_sync",
                    service_name=self.name,
                    endpoint=None,
                    status_code=503,  # Service Unavailable
                    request_id=None,
                    latency_ms=None,
                ),
            )

        # Try to execute the function
        result = func(*args, **kwargs)
        self._record_success()
        return result

    def get_state(self) -> dict[str, Any]:
        """Get current circuit breaker state for monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_exception": str(self.last_exception) if self.last_exception else None,
        }


class RetryWithCircuitBreaker:
    """
    Combines retry logic with circuit breaker pattern.

    This handles both transient failures (with retries) and
    persistent failures (with circuit breaking).
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        retryable_exceptions: tuple[type[Exception], ...] = (
            RateLimitError,
            TimeoutError,
        ),
    ):
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
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
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

        for _attempt in range(1, self.max_retries + 1):
            # Check circuit breaker state first
            if self.circuit_breaker.state == CircuitState.OPEN and not self.circuit_breaker._should_attempt_reset():
                # Circuit is open and not ready to reset
                raise ServiceError(
                    message=f"Circuit breaker '{self.circuit_breaker.name}' is open",
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

            # Attempt the call
            result = await self.circuit_breaker.call_async(func, *args, **kwargs)
            return result

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
