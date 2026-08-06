"""配置解析与边界校验测试。"""

import re

from astrbot_plugin_whleague_negotiation_system.config.defaults import (
    DEFAULT_CONFIG,
    parse_group_list,
    validate_and_cast,
)
from astrbot_plugin_whleague_negotiation_system.utils.security import (
    format_wage,
    generate_auth_code,
    parse_float_2dp,
)


def _expect_error(key, raw):
    try:
        validate_and_cast(key, raw)
        return False
    except ValueError:
        return True


def test_defaults_shape():
    assert DEFAULT_CONFIG["growth_age"] == 25
    assert DEFAULT_CONFIG["negotiation_max_attempts"] == 3
    assert DEFAULT_CONFIG["wage_min"] == 0.01
    assert DEFAULT_CONFIG["wage_max"] == 20.0
    assert DEFAULT_CONFIG["auth_code_default_expire_hours"] == 24
    assert DEFAULT_CONFIG["auth_code_attempt_limit"] == 3


def test_int_bounds():
    assert validate_and_cast("negotiation_max_attempts", "3") == 3
    assert _expect_error("negotiation_max_attempts", "0")
    assert _expect_error("negotiation_max_attempts", "11")
    assert _expect_error("growth_age", "0")
    assert _expect_error("growth_age", "61")
    assert _expect_error("import_col_uid", "31")


def test_float_bounds():
    assert validate_and_cast("wage_min", "0.05") == 0.05
    assert _expect_error("wage_min", "-1")
    assert _expect_error("attempt_decay_multiplier", "1.5")
    assert _expect_error("wage_param_a", "0")


def test_time_and_bool():
    assert validate_and_cast("backup_time", "05:30") == "05:30"
    assert _expect_error("backup_time", "25:00")
    assert validate_and_cast("backup_enabled", "false") is False
    assert validate_and_cast("import_require_confirm", "1") is True


def test_list_parse():
    assert parse_group_list("123,456") == ["123", "456"]
    assert parse_group_list('["1","2"]') == ["1", "2"]
    assert parse_group_list("") == []
    assert parse_group_list(["a", "b"]) == ["a", "b"]


def test_parse_float_2dp():
    assert parse_float_2dp("1.5") == 1.5
    assert parse_float_2dp("0.01") == 0.01
    assert parse_float_2dp("20") == 20.0
    assert parse_float_2dp("1.23", min_val=0.01, max_val=20.0) == 1.23
    for bad in ("1.005", "1.999", "-1", "abc", "1.2.3", "", "1e3", "0.001"):
        try:
            parse_float_2dp(bad)
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass
    try:
        parse_float_2dp("21", max_val=20.0)
        assert False, "accepted 21 with max 20"
    except ValueError:
        pass


def test_format_wage():
    assert format_wage(2.0) == "2"
    assert format_wage(3.1) == "3.1"
    assert format_wage(0.01) == "0.01"
    assert format_wage(20.0) == "20"
    assert format_wage(2.93) == "2.93"


def test_auth_code_charset():
    pattern = re.compile(r"^[A-HJ-NP-Za-km-np-z2-9]{8}$")
    for _ in range(200):
        code = generate_auth_code()
        assert pattern.match(code), code
        assert not any(c in code for c in "0O1lI"), code


def test_tier_thresholds():
    assert validate_and_cast("tier_thresholds", "0.25,0.6,0.9") == "0.25,0.6,0.9"
    assert validate_and_cast("tier_thresholds", "0.1, 0.5, 0.95") == "0.1,0.5,0.95"
    for bad in ("0.25,0.6", "a,0.6,0.9", "0.9,0.6,0.25", "0,0.6,0.9", "1.5,0.6,0.9", ""):
        try:
            validate_and_cast("tier_thresholds", bad)
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass


def test_agent_tiers():
    good = '{"1":{"threshold":0.15,"probability":0.3},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.7}}'
    assert validate_and_cast("agent_tiers", good) == good
    shuffled = '{"3":{"probability":0.7,"threshold":0.35},"1":{"threshold":0.15,"probability":0.3},"2":{"threshold":0.25,"probability":0.5}}'
    assert validate_and_cast("agent_tiers", shuffled) == good
    for bad in (
        "not json",
        "[]",
        '{"1":{},"2":{},"3":{}}',
        '{"1":{"threshold":0.5,"probability":0.3},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.7}}',
        '{"1":{"threshold":0.15,"probability":0.7},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.3}}',
        '{"1":{"threshold":0.15,"probability":0.3},"2":{"threshold":0.25,"probability":0.5}}',
        '{"1":{"threshold":0,"probability":0.3},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.7}}',
        '{"1":{"threshold":1.5,"probability":0.3},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.7}}',
        '{"1":{"threshold":"x","probability":0.3},"2":{"threshold":0.25,"probability":0.5},"3":{"threshold":0.35,"probability":0.7}}',
    ):
        try:
            validate_and_cast("agent_tiers", bad)
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass


def test_agent_change_probability():
    assert validate_and_cast("agent_change_probability", "0.3") == 0.3
    assert validate_and_cast("agent_change_probability", "1") == 1.0
    for bad in ("0", "-0.1", "1.1", "abc", ""):
        try:
            validate_and_cast("agent_change_probability", bad)
            assert False, f"accepted {bad!r}"
        except ValueError:
            pass


def run_all():
    tests = [
        test_defaults_shape,
        test_int_bounds,
        test_float_bounds,
        test_time_and_bool,
        test_list_parse,
        test_parse_float_2dp,
        test_format_wage,
        test_auth_code_charset,
        test_tier_thresholds,
        test_agent_tiers,
        test_agent_change_probability,
    ]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    return len(tests)
