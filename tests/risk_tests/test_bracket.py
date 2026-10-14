"""risk.bracket: stop = entry ∓ k·sigma, target = entry ± 2k·sigma."""

import pytest

from risk.bracket import REWARD_RISK, Bracket, bracket


def test_long():
    b = bracket(entry=2000.0, direction="long", sigma=5.0, k=1.5)
    assert b == Bracket("long", 2000.0, 1992.5, 2015.0, 1.5, 5.0)


def test_short_mirrors_long():
    b = bracket(entry=2000.0, direction="short", sigma=5.0, k=1.5)
    assert (b.stop, b.target) == (2007.5, 1985.0)


def test_target_is_twice_the_stop_distance():
    assert REWARD_RISK == 2.0
    for direction in ("long", "short"):
        b = bracket(entry=1823.37, direction=direction, sigma=3.21, k=0.7)
        assert abs(b.target - b.entry) == pytest.approx(2 * abs(b.entry - b.stop), rel=1e-12)


@pytest.mark.parametrize("direction", ["none", "LONG", "", None, "buy"])
def test_direction_must_be_long_or_short(direction):
    with pytest.raises(ValueError, match="direction"):
        bracket(entry=2000.0, direction=direction, sigma=5.0, k=1.0)


@pytest.mark.parametrize("field", ["entry", "sigma", "k"])
@pytest.mark.parametrize("value", [0, -1.0])
def test_non_positive_inputs_raise(field, value):
    args = dict(entry=2000.0, direction="long", sigma=5.0, k=1.0) | {field: value}
    with pytest.raises(ValueError, match=field):
        bracket(**args)


def test_arguments_are_keyword_only():
    with pytest.raises(TypeError):
        bracket(2000.0, "long", 5.0, 1.0)


# ---------------------------------------------------------------- edge cases

@pytest.mark.parametrize("field", ["entry", "sigma", "k"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1.0", None])
def test_non_finite_or_non_numeric_inputs_raise(field, value):
    args = dict(entry=2000.0, direction="long", sigma=5.0, k=1.0) | {field: value}
    with pytest.raises(ValueError, match=field):
        bracket(**args)


def test_numpy_and_int_inputs_accepted():
    np = pytest.importorskip("numpy")
    b = bracket(entry=np.float64(2000.0), direction="long", sigma=np.float32(5.0), k=2)
    assert (b.stop, b.target) == (1990.0, 2020.0)
    assert all(type(x) is float for x in (b.entry, b.stop, b.target, b.k, b.sigma))


def test_long_stop_at_or_below_zero_raises():
    with pytest.raises(ValueError, match="non-positive"):
        bracket(entry=10.0, direction="long", sigma=5.0, k=2.0)          # stop 0


def test_short_target_at_or_below_zero_raises():
    with pytest.raises(ValueError, match="non-positive"):
        bracket(entry=10.0, direction="short", sigma=5.0, k=1.0)         # target 0


def test_distance_too_small_to_move_the_price_raises():
    with pytest.raises(ValueError, match="no usable stop"):
        bracket(entry=2000.0, direction="long", sigma=1e-300, k=1e-20)   # stop == entry


def test_overflowing_distance_raises():
    with pytest.raises(ValueError, match="no usable stop"):
        bracket(entry=2000.0, direction="short", sigma=1e308, k=10.0)    # k·sigma = inf


def test_bracket_is_immutable():
    b = bracket(entry=2000.0, direction="long", sigma=5.0, k=1.0)
    with pytest.raises(AttributeError):
        b.stop = 1.0
