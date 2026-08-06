import math

from astrbot_plugin_whleague_negotiation_system.services.formula import (
    ability_level,
    attempt_expected,
    direct_fail_check,
    expected_wage,
    rating_level,
    release_fee_bounds,
    success_rate,
    success_tier,
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


def test_rating_level_extremes():
    assert rating_level(200) == 10
    assert rating_level(0) == 1
    assert rating_level(-5) == 1
    assert rating_level(59) == 1
    assert rating_level(60) == 2
    assert rating_level(93) == 10


def test_expected_wage_stability():
    v = expected_wage(10.0, 300, 0.02, 1.9, 0.45)
    assert math.isfinite(v) and v > 0
    v2 = expected_wage(1.0, 1, 0.02, 1.9, 0.45)
    assert math.isfinite(v2) and v2 > 0
    v3 = expected_wage(6.5, 1000000000, 0.02, 1.9, 0.45)
    assert math.isfinite(v3)


def test_success_rate_extremes():
    assert success_rate(0.01, 0.001) == 1.0
    assert success_rate(20.0, 0.02) == 1.0
    assert success_rate(0.01, 20.0) > 0.0
    assert success_rate(0.02, 0.01) == 1.0
    assert success_rate(5.0, 10.0) < success_rate(9.0, 10.0)


def test_attempt_expected_edges():
    assert attempt_expected(5.0, 1, 1.0) == 5.0
    assert attempt_expected(5.0, 2, 1.0) == 5.0
    assert attempt_expected(5.0, 5, 0.95) == 4.75


def test_fuzz_1000():
    import random

    rng = random.Random(42)
    for _ in range(1000):
        level = rng.uniform(1.0, 10.0)
        fee = rng.randint(1, 300)
        ca = rng.randint(0, 200)
        pa = rng.randint(0, 200)
        age = rng.randint(14, 50)
        growth = rng.randint(10, 30)
        offered = rng.uniform(0.01, 20.0)
        expected = expected_wage(level, fee, 0.02, 1.9, 0.45)
        assert math.isfinite(expected) and expected > 0, (level, fee)
        p = success_rate(offered, expected)
        assert 0.0 <= p <= 1.0, (offered, expected)
        lv = ability_level(ca, pa, age, growth)
        assert 1.0 <= lv <= 10.0
        assert rating_level(ca) in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def test_success_tier():
    assert "🥶" in success_tier(0.0)
    assert "🥶" in success_tier(0.24)
    assert "🤔" in success_tier(0.25)
    assert "🤔" in success_tier(0.59)
    assert "😄" in success_tier(0.6)
    assert "😄" in success_tier(0.89)
    assert "🎉" in success_tier(0.9)
    assert "🎉" in success_tier(1.0)
    assert success_tier(0.24) != success_tier(0.25)


def test_success_tier_custom():
    t = (0.3, 0.5, 0.8)
    assert "🥶" in success_tier(0.29, t)
    assert "🤔" in success_tier(0.3, t)
    assert "😄" in success_tier(0.5, t)
    assert "🎉" in success_tier(0.8, t)
    assert "🎉" in success_tier(0.99, t)


def test_direct_fail_check():
    th, pr = 0.25, 0.5
    assert direct_fail_check(0.3, th, pr, 0.0) is False
    assert direct_fail_check(0.25, th, pr, 0.0) is False
    assert direct_fail_check(0.2, th, pr, 0.0) is True
    assert direct_fail_check(0.2, th, pr, 0.49) is True
    assert direct_fail_check(0.2, th, pr, 0.5) is False
    assert direct_fail_check(0.2, th, pr, 0.99) is False
    assert direct_fail_check(1.0, th, pr, 0.0) is False
    assert direct_fail_check(0.0, th, pr, 0.0) is True
    assert direct_fail_check(0.1, 0.35, 0.7, 0.69) is True
    assert direct_fail_check(0.1, 0.35, 0.7, 0.7) is False


def run_all():
    tests = [
        test_rating_level,
        test_ability_level,
        test_release_fee_bounds,
        test_expected_wage_values,
        test_success_rate_values,
        test_attempt_expected,
        test_monotonic_ratio,
        test_rating_level_extremes,
        test_expected_wage_stability,
        test_success_rate_extremes,
        test_attempt_expected_edges,
        test_fuzz_1000,
        test_success_tier,
        test_success_tier_custom,
        test_direct_fail_check,
    ]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    return len(tests)
