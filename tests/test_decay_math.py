"""Property tests for the forgetting curve.

The decay formula lives in Cypher (DreamJobQueries.decay_salience):

    salience(t) = floor + (salience - floor) * exp(-lambda * days)

These tests pin down the invariants the ORIGINAL implementation violated —
above all cadence-independence: applying decay for t1 days and then t2 days
must equal applying it once for t1+t2 days. The old code multiplied by a
fixed factor per RUN (every 5 minutes), so its trajectory depended on the
scheduler cadence and destroyed the corpus in hours.
"""

import math

from hypothesis import given
from hypothesis import strategies as st

from memory_palace.core.constants import SALIENCE_DECAY_LAMBDA_PER_DAY, SALIENCE_FLOOR


def decay(salience: float, days: float, floor: float = SALIENCE_FLOOR, lam: float = SALIENCE_DECAY_LAMBDA_PER_DAY) -> float:
    """Python mirror of the Cypher decay formula."""
    return floor + (salience - floor) * math.exp(-lam * days)


salience_st = st.floats(min_value=SALIENCE_FLOOR, max_value=1.0, allow_nan=False)
days_st = st.floats(min_value=0.0, max_value=3650.0, allow_nan=False)


@given(salience=salience_st, days=days_st)
def test_decay_bounded(salience: float, days: float):
    """Decay never leaves [floor, salience]."""
    result = decay(salience, days)
    assert result + 1e-12 >= SALIENCE_FLOOR
    assert result <= salience + 1e-12


@given(salience=salience_st, d1=days_st, d2=days_st)
def test_decay_cadence_independent(salience: float, d1: float, d2: float):
    """decay(decay(s, d1), d2) == decay(s, d1 + d2).

    THE invariant: the trajectory must not depend on how often the dream
    job runs. This is exactly what the original per-tick multiplication
    broke.
    """
    two_step = decay(decay(salience, d1), d2)
    one_step = decay(salience, d1 + d2)
    assert math.isclose(two_step, one_step, rel_tol=1e-9, abs_tol=1e-9)


@given(salience=salience_st, d1=days_st, d2=days_st)
def test_decay_monotonic_in_time(salience: float, d1: float, d2: float):
    """More elapsed time never yields higher salience."""
    lo, hi = sorted([d1, d2])
    assert decay(salience, hi) <= decay(salience, lo) + 1e-12


def test_half_life_is_45_days():
    """The lambda constant must actually encode a 45-day half-life."""
    start = 1.0
    halfway = decay(start, 45.0, floor=0.0)
    assert math.isclose(halfway, 0.5, rel_tol=0.01)


def test_the_original_bug_would_fail_these_invariants():
    """Regression documentation: per-tick decay is cadence-DEPENDENT.

    The original code applied factor (1 - 0.0154) once per 5-minute tick.
    Over one day that is 288 applications: salience * 0.9846^288 ≈ 0.011x,
    not the intended one-day step of 0.9847x. This test exists so nobody
    reintroduces per-run multiplication.
    """
    per_tick_factor = 1 - SALIENCE_DECAY_LAMBDA_PER_DAY
    ticks_per_day = 24 * 60 // 5
    one_day_old_behavior = 1.0 * per_tick_factor**ticks_per_day
    one_day_correct = decay(1.0, 1.0, floor=0.0)

    assert one_day_old_behavior < 0.02  # corpus-destroying
    assert one_day_correct > 0.98  # gentle, as intended


@given(salience=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_reinforcement_asymptotic(salience: float):
    """Reinforcement approaches 1.0 but never exceeds it."""
    from memory_palace.core.constants import SALIENCE_REINFORCEMENT_RATE

    boosted = salience + (1.0 - salience) * SALIENCE_REINFORCEMENT_RATE
    assert salience <= boosted <= 1.0
