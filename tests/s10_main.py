"""main.py 白名单门卫测试（不初始化插件，仅测纯逻辑）。"""

import asyncio
import types

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.main import NegotiationSystemPlugin


class _GateEvent:
    def __init__(self, group_id):
        self._gid = group_id

    def get_group_id(self):
        return self._gid


def _make_plugin(groups, code_groups):
    p = NegotiationSystemPlugin(None)
    p.config_cache = {
        "group_whitelist": list(groups),
        "auth_code_group_whitelist": list(code_groups),
    }
    return p


def test_group_gate_empty():
    p = _make_plugin([], [])
    assert p.is_group_allowed(_GateEvent("g1")) is True
    assert p.is_group_allowed(_GateEvent("g9")) is True
    assert p.is_group_allowed(_GateEvent(None)) is False


def test_group_gate_whitelist():
    p = _make_plugin(["g1", "g2"], [])
    assert p.is_group_allowed(_GateEvent("g1")) is True
    assert p.is_group_allowed(_GateEvent("g3")) is False
    assert p.is_group_allowed(_GateEvent(None)) is False


def test_code_gate_follows_group():
    p = _make_plugin(["g1"], [])
    assert p.is_code_group_allowed(_GateEvent("g1")) is True
    assert p.is_code_group_allowed(_GateEvent("gX")) is False


def test_code_gate_independent():
    p = _make_plugin(["g1", "g2"], ["g2"])
    assert p.is_code_group_allowed(_GateEvent("g2")) is True
    assert p.is_code_group_allowed(_GateEvent("g1")) is False


def run_all():
    tests = [
        test_group_gate_empty,
        test_group_gate_whitelist,
        test_code_gate_follows_group,
        test_code_gate_independent,
    ]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    return len(tests)
