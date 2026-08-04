import math

from astrbot_plugin_whleague_negotiation_system.services.formula import (
    ability_level,
    attempt_expected,
    expected_wage,
    rating_level,
    release_fee_bounds,
    success_rate,
)


def test_rating_level():
    cases = {
        93: 10, 95: 10, 100: 10,
        90: 9, 91: 9, 92: 9,
        87: 8, 88: 8, 89: 8,
        84: 7, 85: 7, 86: 7,
        80: 6, 81: 6, 82: 6, 83: 6,
        75: 5, 76: 5, 77: 5, 78: 5, 79: 5,
        70: 4, 71: 4, 72: 4, 73: 4, 74: 4,
        65: 3, 66: 3, 67: 3, 68: 3, 69: 3,
        60: 2, 61: 2, 62: 2, 63: 2, 64: 2,
        1: 1, 59: 1,
    }
    for rating, level in cases.items():
        assert rating_level(rating) == level, (rating, level)


def test_ability_level():
    assert ability_level(93, 87, 20, 25) == 9.0
    assert ability_level(93, 87, 25, 25) == 9.0
    assert ability_level(93, 87, 26, 25) == 10.0
    assert ability_level(80, 93, 18, 25) == 8.0
    assert ability_level(84, 83, 18, 25) == 6.5
    assert ability_level(65, 93, 30, 25) == 3.0


def test_release_fee_bounds():
    assert release_fee_bounds(15) == (5, 25)
    assert release_fee_bounds(10) == (1, 20)
    assert release_fee_bounds(20) == (10, 30)
    assert release_fee_bounds(21) == (11, 31)
    assert release_fee_bounds(100) == (50, 150)
    assert release_fee_bounds(201) == (101, 301)


def test_expected_wage_values():
    assert abs(expected_wage(8.0, 10, 0.02, 1.9, 0.45) - 2.93) < 0.01
    assert abs(expected_wage(10.0, 1, 0.02, 1.9, 0.45) - 1.59) < 0.01
    assert expected_wage(5.0, 5, 0.02, 1.9, 0.45) > 0
    assert abs(expected_wage(6.5, 3, 0.02, 1.9, 0.45) - 1.15) < 0.01


def test_success_rate_values():
    assert success_rate(0, 10.0) == 0.0
    assert success_rate(5.0, 0) == 0.0
    assert success_rate(10.0, 10.0) == 1.0
    assert success_rate(11.0, 10.0) == 1.0
    assert abs(success_rate(8.0, 10.0) - 0.5) < 1e-9
    assert abs(success_rate(7.0, 10.0) - 0.0953) < 0.0005
    assert abs(success_rate(5.0, 10.0) - 0.0012) < 0.0005
    assert 0 < success_rate(6.0, 10.0) < 1.0


def test_attempt_expected():
    assert attempt_expected(10.0, 1, 0.95) == 10.0
    assert attempt_expected(10.0, 2, 0.95) == 9.5
    assert attempt_expected(10.0, 3, 0.95) == 9.5
    assert attempt_expected(3.33, 2, 0.95) == 3.16


def test_monotonic_ratio():
    lo = success_rate(5.0, 10.0)
    mid = success_rate(8.0, 10.0)
    hi = success_rate(9.9, 10.0)
    assert lo < mid < hi
    assert math.isfinite(hi)


def run_all():
    tests = [
        test_rating_level,
        test_ability_level,
        test_release_fee_bounds,
        test_expected_wage_values,
        test_success_rate_values,
        test_attempt_expected,
        test_monotonic_ratio,
    ]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    return len(tests)
