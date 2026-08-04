"""能力等级、违约金合法区间与内部判定算法。

本模块包含两类内容：
- 公开规则：能力等级映射表、成长年龄判定、违约金调整区间；
- 内部算法：预期工资与报价成败判定，规则不公开，实现零注释。

"""

import math


def rating_level(rating: int) -> int:
    """CA/PA → 能力等级映射（公开规则）。"""
    if rating >= 93:
        return 10
    if rating >= 90:
        return 9
    if rating >= 87:
        return 8
    if rating >= 84:
        return 7
    if rating >= 80:
        return 6
    if rating >= 75:
        return 5
    if rating >= 70:
        return 4
    if rating >= 65:
        return 3
    if rating >= 60:
        return 2
    return 1


def ability_level(ca: int, pa: int, age: int, growth_age: int) -> float:
    """能力等级（公开规则）。

    年龄 ≤ 成长年龄视为可成长球员，取 (CA等级 + PA等级) / 2；否则取 CA等级。
    """
    ca_level = rating_level(ca)
    if age <= growth_age:
        return (ca_level + rating_level(pa)) / 2.0
    return float(ca_level)


def release_fee_bounds(old_fee: int) -> tuple[int, int]:
    """新违约金的合法区间（公开规则）。

    旧违约金 ≤ 20 时在 ±10 内调整；> 20 时在 ±50% 内调整；下限至少 1。
    """
    if old_fee <= 20:
        low = max(1, old_fee - 10)
        high = old_fee + 10
    else:
        low = max(1, math.ceil(old_fee * 0.5))
        high = math.floor(old_fee * 1.5)
    return low, high


def expected_wage(level: float, release_fee: int, a: float, b: float, c: float):
    return round(a * (level ** b) * (release_fee ** c), 2)


def attempt_expected(base: float, attempt_no: int, mult: float):
    if attempt_no > 1:
        return round(base * mult, 2)
    return base


def success_rate(offered: float, expected: float):
    if offered <= 0 or expected <= 0:
        return 0.0
    ratio = offered / expected
    if ratio >= 1.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-9.0 * ((ratio - 0.6) / 0.4 - 0.5)))


def success_tier(p: float, thresholds=(0.25, 0.6, 0.9)):
    lo, mid, hi = thresholds
    if p >= hi:
        return "🎉 几乎必成"
    if p >= mid:
        return "😄 机会较大"
    if p >= lo:
        return "🤔 有一定机会"
    return "🥶 希望渺茫"
