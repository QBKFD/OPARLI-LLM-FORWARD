"""risk.sizing: fixed-fractional size, rounded down to the increment."""

import pytest

from risk.sizing import (MAX_RISK_PCT, XAUUSD_SIZE_INCREMENT, XAUUSD_USD_PER_UNIT_MOVE,
                         position_size)

BASE = dict(equity=100_000.0, risk_pct=0.5, entry=2000.0, stop=1990.0,
            usd_per_unit_move=1.0, size_increment=1.0)


def size(**kw):
    return position_size(**(BASE | kw))


def test_worked_example():
    # 0.5 % of 100k = 500 USD at risk; 10 USD per unit to the stop -> 50 units
    assert size() == 50.0


def test_rounds_down_to_the_increment():
    assert size(stop=1993.0) == 71.0            # 500 / 7 = 71.43
    assert size(stop=1993.0, size_increment=0.01) == 71.42
    assert size(stop=1993.0, size_increment=5.0) == 70.0


def test_short_side_same_distance_same_size():
    assert size(stop=2010.0) == size()


def test_multiplier_scales_size():
    assert size(usd_per_unit_move=100.0, size_increment=0.01) == 0.5


def test_float_difference_does_not_cost_an_increment():
    # 2058.59 - 2038.59 is 20.000000000000227 in floats, so plain float maths gives
    # 1000 / 20.000000000000227 = 49.99999999999943 -> 49 units instead of 50
    assert size(risk_pct=1.0, entry=2058.59, stop=2038.59) == 50.0
    assert size(risk_pct=1.0, entry=2058.59, stop=2038.59, size_increment=0.01) == 50.0
    # 50 USD / 25.000000000000227 -> 1 instead of 2
    assert size(equity=10_000.0, entry=2058.82, stop=2033.82) == 2.0
    # an increment that is not a binary fraction: 0.3 / 0.1 is 2.9999999999999996
    assert size(equity=60_000.0, risk_pct=0.5, entry=2000.0, stop=1000.0,
                size_increment=0.1) == 0.3


def test_budget_below_one_increment_is_zero():
    assert size(equity=1_000.0, risk_pct=0.1, stop=1980.0) == 0.0     # 1 USD / 20 = 0.05


def test_stop_equal_to_entry_raises():
    with pytest.raises(ValueError, match="stop equals entry"):
        size(stop=2000.0)


@pytest.mark.parametrize("field", ["equity", "risk_pct", "entry", "stop",
                                   "usd_per_unit_move", "size_increment"])
@pytest.mark.parametrize("value", [0.0, -1.0])
def test_non_positive_values_raise(field, value):
    with pytest.raises(ValueError, match=field):
        size(**{field: value})


def test_risk_pct_cap():
    assert MAX_RISK_PCT == 5.0
    assert size(risk_pct=5.0) == 500.0
    with pytest.raises(ValueError, match="percent"):
        size(risk_pct=50.0)                     # typo for 0.5
    with pytest.raises(ValueError, match="percent"):
        size(risk_pct=5.000001)


def test_xauusd_constants_are_explicit():
    assert (XAUUSD_USD_PER_UNIT_MOVE, XAUUSD_SIZE_INCREMENT) == (1.0, 1.0)


def test_arguments_are_keyword_only_and_required():
    with pytest.raises(TypeError):
        position_size(100_000.0, 0.5, 2000.0, 1990.0, 1.0, 1.0)
    partial = dict(BASE)
    del partial["size_increment"]
    with pytest.raises(TypeError):
        position_size(**partial)
