"""State-machine coverage for external-service circuit breaking."""

import asyncio

import pytest

from memory_palace.core.circuit_breaker import CircuitBreaker, CircuitState
from memory_palace.core.errors import ServiceError, TimeoutError


async def test_expected_failures_open_the_circuit() -> None:
    breaker: CircuitBreaker[None] = CircuitBreaker(
        name="test",
        failure_threshold=2,
        expected_exception_types=(TimeoutError,),
    )

    async def fail() -> None:
        raise TimeoutError("timed out")

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await breaker.call_async(fail)

    assert breaker.state is CircuitState.OPEN
    assert breaker.failure_count == 2


async def test_unexpected_programming_error_does_not_trip_external_circuit() -> None:
    breaker: CircuitBreaker[None] = CircuitBreaker(
        name="test",
        failure_threshold=1,
        expected_exception_types=(TimeoutError,),
    )

    async def fail() -> None:
        raise ValueError("bug")

    with pytest.raises(ValueError):
        await breaker.call_async(fail)

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


async def test_half_open_allows_only_one_probe() -> None:
    breaker: CircuitBreaker[None] = CircuitBreaker(name="test", success_threshold=1)
    breaker.state = CircuitState.HALF_OPEN
    started = asyncio.Event()
    release = asyncio.Event()

    async def probe() -> None:
        started.set()
        await release.wait()

    first = asyncio.create_task(breaker.call_async(probe))
    await started.wait()
    with pytest.raises(ServiceError):
        await breaker.call_async(probe)
    release.set()
    await first

    assert breaker.state is CircuitState.CLOSED


async def test_cancelled_half_open_probe_releases_probe_slot() -> None:
    breaker: CircuitBreaker[None] = CircuitBreaker(name="test", success_threshold=1)
    breaker.state = CircuitState.HALF_OPEN
    started = asyncio.Event()

    async def probe() -> None:
        started.set()
        await asyncio.Event().wait()

    cancelled_probe = asyncio.create_task(breaker.call_async(probe))
    await started.wait()
    cancelled_probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_probe

    await breaker.call_async(lambda: asyncio.sleep(0))
    assert breaker.state is CircuitState.CLOSED


def test_sync_half_open_probe_transitions_to_closed() -> None:
    breaker: CircuitBreaker[str] = CircuitBreaker(name="test", success_threshold=1)
    breaker.state = CircuitState.HALF_OPEN

    assert breaker.call_sync(lambda: "ok") == "ok"
    assert breaker.state is CircuitState.CLOSED
