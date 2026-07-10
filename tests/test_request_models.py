"""Cost and shape bounds for the public memory API."""

import pytest
from pydantic import ValidationError

from memory_palace.api.endpoints.memory import (
    MAX_BATCH_MEMORIES,
    MAX_MEMORY_CONTENT_CHARS,
    SearchRequest,
    StoreBatchRequest,
    StoreMemoryRequest,
)


def test_memory_content_must_be_nonempty_and_bounded() -> None:
    with pytest.raises(ValidationError):
        StoreMemoryRequest(content="   ", role="user")
    with pytest.raises(ValidationError):
        StoreMemoryRequest(content="x" * 32_769, role="user")


def test_memory_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StoreMemoryRequest.model_validate({"content": "hello", "role": "user", "admin": True})


def test_batch_size_is_bounded() -> None:
    memory = StoreMemoryRequest(content="hello", role="user")

    with pytest.raises(ValidationError):
        StoreBatchRequest(memories=[])
    with pytest.raises(ValidationError):
        StoreBatchRequest(memories=[memory] * 51)


def test_batch_aggregate_content_fits_http_body_budget() -> None:
    with pytest.raises(ValidationError, match="request-body budget"):
        StoreBatchRequest(
            memories=[
                StoreMemoryRequest(content="x" * MAX_MEMORY_CONTENT_CHARS, role="user")
                for _ in range(MAX_BATCH_MEMORIES)
            ]
        )


@pytest.mark.parametrize(
    "values",
    [
        {"query": ""},
        {"query": "x", "k": 0},
        {"query": "x", "k": 51},
        {"query": "x", "threshold": -0.1},
        {"query": "x", "threshold": 1.1},
        {"query": "x", "min_salience": 2.0},
        {"query": "x", "topic_ids": list(range(101))},
    ],
)
def test_recall_cost_and_score_inputs_are_bounded(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(values)
