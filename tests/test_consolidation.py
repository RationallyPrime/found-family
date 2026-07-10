"""Consolidation preserves the source lifecycle domain values."""

from memory_palace.services.consolidation import _lifecycle_value


def test_explicit_zero_is_not_replaced_by_the_missing_value_default() -> None:
    assert _lifecycle_value({"salience": 0.0}, "salience", 0.3) == 0.0
    assert _lifecycle_value({"salience": None}, "salience", 0.3) == 0.3
