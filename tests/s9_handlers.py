"""Handler 层测试：参数解析、权限拒绝、统一命名指令。"""

import asyncio
import types
from unittest import mock

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.handlers.admin import AdminHandler
from astrbot_plugin_whleague_negotiation_system.handlers.player import PlayerHandler


class FakeEvent:
    def __init__(self, qq, msg, is_admin=True, group_id="g1"):
        self._qq = qq
        self._msg = msg
        self._admin = is_admin
        self._gid = group_id
        self.results = []

    def get_sender_id(self):
        return self._qq

    def get_group_id(self):
        return self._gid

    def get_message_str(self):
        return self._msg

    def is_admin(self):
        return self._admin

    def plain_result(self, text):
        r = types.SimpleNamespace(text=text)
        self.results.append(r)
        return r


class FakePlugin:
    def __init__(self, env: TestEnv):
        self.dao = env.dao
        self.negotiation_service = env.service
        self.import_service = env.import_service
        self.config_cache = env.cfg
        self.rate_limiter = env.limiter


async def _collect(agen):
    out = []
    async for r in agen:
        out.append(r.text)
    return out


async def _setup_team(env: TestEnv, team="甲队", qq="90001", bind=True):
    await env.service.create_team(team, "admin1")
    code = await env.service.generate_code(team, "admin1", hours=1)
    if bind:
        await env.service.bind_team(qq, code["code"], "g1")
    return code


async def test_permission_denied(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    event = FakeEvent("99999", "创建球队 乙队", is_admin=False)
    texts = await _collect(handler.create_team(event))
    assert texts == ["你没有权限执行此操作"]


async def test_bind_usage(env: TestEnv):
    plugin = FakePlugin(env)
    handler = PlayerHandler(plugin)
    code = await _setup_team(env, bind=False)
    event = FakeEvent("90002", f"绑定球队 {code['code']}")
    texts = await _collect(handler.bind_team(event))
    assert texts == [f"✅ 认证成功！你已绑定球队「甲队」"]
    event2 = FakeEvent("90003", "绑定球队")
    texts2 = await _collect(handler.bind_team(event2))
    assert "用法: /绑定球队 <认证码>" in texts2[0]


async def test_unbind_qq(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    await _setup_team(env, team="解绑队", qq="90001")
    event = FakeEvent("admin1", "解绑 90001")
    texts = await _collect(handler.unbind(event))
    assert "✅ 已解绑 90001" in texts[0]


async def test_unbind_team_multi_warning(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    await env.service.create_team("多人队2", "admin1")
    for i, qq in enumerate(("91001", "91002")):
        code = await env.service.generate_code("多人队2", "admin1", hours=1)
        await env.service.bind_team(qq, code["code"], "g1")
    event = FakeEvent("admin1", "解绑 多人队2")
    texts = await _collect(handler.unbind(event))
    assert "请指定要解绑的 QQ" in texts[0]


async def test_unbind_team_single(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    await _setup_team(env, team="单解队", qq="92001")
    event = FakeEvent("admin1", "解绑 单解队")
    texts = await _collect(handler.unbind(event))
    assert "✅ 已解绑" in texts[0]


async def test_create_case_order(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    await env.import_service.import_rows([("555", "Order", "")], "admin1")
    event = FakeEvent("admin1", "创建谈判 甲队 555 20 85 88 15")
    texts = await _collect(handler.create_case(event))
    assert "✅ 已创建谈判案例" in texts[0]
    case_row = await env.dao.get_case_by_id(1)
    team = await env.dao.get_team_by_name("甲队")
    player = await env.dao.get_player_by_uid("555")
    assert case_row["team_id"] == team["id"]
    assert case_row["player_id"] == player["id"]
    assert case_row["age"] == 20 and case_row["ca"] == 85 and case_row["pa"] == 88


async def test_name_entity(env: TestEnv):
    plugin = FakePlugin(env)
    handler = AdminHandler(plugin)
    event = FakeEvent("admin1", "命名 窗口 夏窗")
    texts = await _collect(handler.name_entity(event))
    assert "✅ 已命名窗口" in texts[0]
    event2 = FakeEvent("admin1", "命名 赛季 26-27")
    texts2 = await _collect(handler.name_entity(event2))
    assert "✅ 已命名赛季" in texts2[0]
    st = await env.service.league_status()
    assert st["window_name"] == "夏窗"
    assert st["season_name"] == "26-27"
    # 非法目标词
    event3 = FakeEvent("admin1", "命名 球场 夏窗")
    texts3 = await _collect(handler.name_entity(event3))
    assert "用法: /命名 <窗口|赛季> <名称>" in texts3[0]
    # 缺名称
    event4 = FakeEvent("admin1", "命名 窗口")
    texts4 = await _collect(handler.name_entity(event4))
    assert "用法: /命名 <窗口|赛季> <名称>" in texts4[0]


async def test_advance_window_label_consistency(env: TestEnv):
    svc = env.service
    await svc.close_window_cases()
    result = await svc.advance_window("admin1")
    state = await env.dao.get_league_state()
    assert result["window_seq"] == state["window_seq"]


async def _setup_player_env(env: TestEnv, team="冒烟队", qq="93001"):
    plugin = FakePlugin(env)
    handler = PlayerHandler(plugin)
    await env.service.create_team(team, "admin1")
    code = await env.service.generate_code(team, "admin1", hours=1)
    await env.service.bind_team(qq, code["code"], "g1")
    await env.import_service.import_rows([("595", "Smoke", "")], "admin1")
    case = await env.service.create_case("595", team, 20, 85, 88, 15, "admin1")
    return plugin, handler, case


async def test_player_my_team(env: TestEnv):
    plugin, handler, _ = await _setup_player_env(env, team="冒烟队A", qq="93001")
    event = FakeEvent("93001", "我的球队")
    texts = await _collect(handler.my_team(event))
    assert "我的球队: 冒烟队A" in texts[0]
    event2 = FakeEvent("93009", "我的球队")
    texts2 = await _collect(handler.my_team(event2))
    assert "尚未绑定球队" in texts2[0]


async def test_player_pending_cases(env: TestEnv):
    plugin, handler, case = await _setup_player_env(env, team="冒烟队B", qq="93002")
    event = FakeEvent("93002", "待谈判")
    texts = await _collect(handler.pending_cases(event))
    assert f"#{case['case_id']} Smoke" in texts[0]
    assert "开始谈判" in texts[0]


async def test_player_start_negotiation(env: TestEnv):
    plugin, handler, case = await _setup_player_env(env, team="冒烟队C", qq="93003")
    event = FakeEvent("93003", f"开始谈判 {case['case_id']} 18")
    texts = await _collect(handler.start_negotiation(event))
    assert "谈判已开始" in texts[0]
    assert "预期工资" in texts[0]
    event2 = FakeEvent("93003", "开始谈判")
    texts2 = await _collect(handler.start_negotiation(event2))
    assert "用法: /开始谈判" in texts2[0]


async def test_player_offer_success(env: TestEnv):
    plugin, handler, case = await _setup_player_env(env, team="冒烟队D", qq="93004")
    await env.service.start_negotiation(case["case_id"], "93004", 18)
    session = await env.dao.get_session_by_case(case["case_id"])
    event = FakeEvent("93004", f"报价 {case['case_id']} {session['expected_wage']:.2f}")
    texts = await _collect(handler.offer(event))
    assert "谈判成功" in texts[0]
    # 已结束会话再报价（先清限流）
    env.limiter.clear()
    event2 = FakeEvent("93004", f"报价 {case['case_id']} {session['expected_wage']:.2f}")
    texts2 = await _collect(handler.offer(event2))
    assert "不在谈判中" in texts2[0]


async def test_player_contracts_and_status(env: TestEnv):
    plugin, handler, case = await _setup_player_env(env, team="冒烟队E", qq="93005")
    event = FakeEvent("93005", "我的合同")
    texts = await _collect(handler.my_contracts(event))
    assert "暂无有效合同" in texts[0]
    event2 = FakeEvent("93005", "赛季状态")
    texts2 = await _collect(handler.league_status(event2))
    assert "当前赛季" in texts2[0]
    assert "转会窗口" in texts2[0]


async def test_player_offer_fail_tier(env: TestEnv):
    plugin, handler, case = await _setup_player_env(env, team="冒烟队F", qq="93006")
    await env.service.start_negotiation(case["case_id"], "93006", 18)
    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        return_value=0.999,
    ):
        event = FakeEvent("93006", f"报价 {case['case_id']} 0.01")
        texts = await _collect(handler.offer(event))
    assert "把握" in texts[0]
    assert "🥶" in texts[0]


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_permission_denied(env)
            print("  PASS test_permission_denied")
            await test_bind_usage(env)
            print("  PASS test_bind_usage")
            await test_unbind_qq(env)
            print("  PASS test_unbind_qq")
            await test_unbind_team_multi_warning(env)
            print("  PASS test_unbind_team_multi_warning")
            await test_unbind_team_single(env)
            print("  PASS test_unbind_team_single")
            await test_create_case_order(env)
            print("  PASS test_create_case_order")
            await test_name_entity(env)
            print("  PASS test_name_entity")
            await test_advance_window_label_consistency(env)
            print("  PASS test_advance_window_label_consistency")
            await test_player_my_team(env)
            print("  PASS test_player_my_team")
            await test_player_pending_cases(env)
            print("  PASS test_player_pending_cases")
            await test_player_start_negotiation(env)
            print("  PASS test_player_start_negotiation")
            await test_player_offer_success(env)
            print("  PASS test_player_offer_success")
            await test_player_contracts_and_status(env)
            print("  PASS test_player_contracts_and_status")
            await test_player_offer_fail_tier(env)
            print("  PASS test_player_offer_fail_tier")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 14
