"""Structural error context remains intact from failure site to handler."""

from memory_palace.core.errors import ProcessingError


def test_mapping_details_are_preserved_without_mutating_the_caller() -> None:
    context = {
        "source": "voyage",
        "operation": "validate",
        "expected_dimensions": 1_024,
        "actual_dimensions": 3,
    }

    error = ProcessingError("invalid vector", details=context)

    assert context == {
        "source": "voyage",
        "operation": "validate",
        "expected_dimensions": 1_024,
        "actual_dimensions": 3,
    }
    assert error.details.source == "voyage"
    assert error.details.operation == "validate"
    assert error.details.metadata == {"expected_dimensions": 1_024, "actual_dimensions": 3}
