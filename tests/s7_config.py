"""配置解析与边界校验测试。"""

from astrbot_plugin_whleague_negotiation_system.config.defaults import (
    DEFAULT_CONFIG,
    parse_group_list,
    validate_and_cast,
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


def run_all():
    tests = [
        test_defaults_shape,
        test_int_bounds,
        test_float_bounds,
        test_time_and_bool,
        test_list_parse,
    ]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    return len(tests)
