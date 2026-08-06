"""核心业务流程端到端测试。"""

import asyncio
from unittest import mock

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.config.defaults import DEFAULT_CONFIG
from astrbot_plugin_whleague_negotiation_system.services.negotiation_service import (
    NegotiationError,
)


async def test_full_flow(env: TestEnv):
    svc = env.service
    dao = env.dao

    # 创建球队
    team = await svc.create_team("测试队", "admin1")
    assert team["name"] == "测试队"
    try:
        await svc.create_team("测试队", "admin1")
        assert False, "duplicate team allowed"
    except NegotiationError:
        pass

    # 生成认证码并绑定（仅认证码）
    code_info = await svc.generate_code("测试队", "admin1", hours=1)
    assert len(code_info["code"]) == 8
    await svc.bind_team("10001", code_info["code"], "g1")

    # 一QQ一队：重复绑定被拒
    code2 = await svc.generate_code("测试队", "admin1", hours=1)
    try:
        await svc.bind_team("10001", code2["code"], "g1")
        assert False, "double bind allowed"
    except NegotiationError:
        pass

    # 无效码
    try:
        await svc.bind_team("10002", "AAAA0000", "g1")
        assert False
    except NegotiationError:
        pass

    # 导入球员
    data = [("111", "Alpha", ""), ("222", "Beta", "")]
    stats = await env.import_service.import_rows(data, "admin1")
    assert stats["added"] == 2
    stats2 = await env.import_service.import_rows([("111", "AlphaV2", "")], "admin1")
    assert stats2["updated"] == 1

    # 创建案例
    case = await svc.create_case("111", "测试队", 20, 85, 88, 15, "admin1")
    case_id = case["case_id"]
    assert case["ref_fee"] is None

    # 独占：重复创建被拒
    try:
        await svc.create_case("111", "测试队", 20, 85, 88, 15, "admin1")
        assert False
    except NegotiationError:
        pass

    # 非绑定成员无法谈判
    try:
        await svc.start_negotiation(case_id, "99999", 18)
        assert False
    except NegotiationError:
        pass

    # 开始谈判（违约金 18，区间 5~25）
    result = await svc.start_negotiation(case_id, "10001", 18)
    assert result["expected_wage"] > 0
    session = await dao.get_session_by_case(case_id)
    assert session["release_fee"] == 18
    assert abs(session["expected_wage"] - result["expected_wage"]) < 1e-9

    # 重设违约金（不占次数）
    result2 = await svc.start_negotiation(case_id, "10001", 20)
    assert result2["expected_wage"] != result["expected_wage"]
    session = await dao.get_session_by_case(case_id)
    assert session["release_fee"] == 20

    # 超出违约金区间
    try:
        await svc.start_negotiation(case_id, "10001", 40)
        assert False
    except NegotiationError:
        pass

    # 报价必须递增
    try:
        await svc.offer_wage(case_id, "10001", 0.01)
        await svc.offer_wage(case_id, "10001", 0.01)
        assert False, "non-increasing offer allowed"
    except NegotiationError:
        pass

    # 保证成功：报价 ≥ 预期工资
    wage_ok = session["expected_wage"]
    offer = await svc.offer_wage(case_id, "10001", wage_ok)
    assert offer["result"] == "success"
    contract = (await dao.list_contracts(1, None))[0]
    assert contract["wage"] == wage_ok
    assert contract["source"] == "negotiation"
    case_row = await dao.get_case_by_id(case_id)
    assert case_row["status"] == "success"

    # 失败后自动成约（3 次小报价）
    case2 = await svc.create_case("222", "测试队", 20, 85, 88, 15, "admin1")
    await svc.start_negotiation(case2["case_id"], "10001", 18)
    s2 = await dao.get_session_by_case(case2["case_id"])
    for w in (0.01, 0.02, 0.03):
        r = await svc.offer_wage(case2["case_id"], "10001", w)
        assert r["result"] in ("fail", "forced")
        if r["result"] == "fail":
            assert r["tier"] in ("🥶 希望渺茫", "🤔 有一定机会", "😄 机会较大", "🎉 几乎必成")
    contracts2 = await dao.list_contracts(1, None)
    forced = [c for c in contracts2 if c["source"] == "forced"]
    assert len(forced) == 1
    assert forced[0]["wage"] == s2["expected_wage"]

    # 我的合同（窗口过滤）
    mine = await svc.my_contracts("10001", None)
    assert mine["total"] == 2

    # 推进窗口：无活跃案例 → 成功
    adv = await svc.advance_window("admin1")
    assert adv["window_seq"] == 2
    mine2 = await svc.my_contracts("10001", None)
    assert mine2["total"] == 0

    # 推进赛季（默认成长年龄-1）
    state0 = await svc.league_status()
    s = await svc.advance_season("admin1")
    assert s["season_number"] == 2
    assert s["window_seq"] == 3
    assert s["growth_age"] == state0["growth_age"] - 1
    assert env.cfg["growth_age"] == s["growth_age"]

    # 推进赛季（恢复）
    s2r = await svc.advance_season("admin1", reset=True)
    assert s2r["growth_age"] == 25

    # 解绑（QQ 路径）
    un = await svc.unbind_qq("10001")
    assert un["qq"] == "10001"
    try:
        await svc.unbind_qq("10001")
        assert False
    except NegotiationError:
        pass


async def test_unbind_team(env: TestEnv):
    svc = env.service
    await svc.create_team("单人队", "admin1")
    code = await svc.generate_code("单人队", "admin1", hours=1)
    await svc.bind_team("30001", code["code"], "g1")

    # 恰好 1 人 → 直接解绑
    un = await svc.unbind_team("单人队")
    assert un["qq"] == "30001"

    # 0 人
    try:
        await svc.unbind_team("单人队")
        assert False
    except NegotiationError as e:
        assert "暂无绑定" in str(e)

    # 2 人 → 警告要求指定 QQ
    await svc.create_team("多人队", "admin1")
    for i, qq in enumerate(("40001", "40002")):
        code = await svc.generate_code("多人队", "admin1", hours=1)
        await svc.bind_team(qq, code["code"], "g1")
    try:
        await svc.unbind_team("多人队")
        assert False
    except NegotiationError as e:
        assert "2 人绑定" in str(e)
    # 指定 QQ 仍可解绑
    un = await svc.unbind_qq("40001")
    assert un["qq"] == "40001"


async def test_window_naming(env: TestEnv):
    svc = env.service
    st0 = await svc.league_status()
    orig_win = st0["window_seq"]
    orig_season = st0["season_number"]
    assert st0["window_name"] == ""
    assert st0["season_name"] == ""

    named = await svc.name_window("夏窗")
    assert named["window_seq"] == orig_win
    assert named["name"] == "夏窗"
    st = await svc.league_status()
    assert st["window_name"] == "夏窗"

    named_s = await svc.name_season("26-27")
    assert named_s["season_number"] == orig_season
    st = await svc.league_status()
    assert st["season_name"] == "26-27"

    # 空名称被拒
    try:
        await svc.name_window("   ")
        assert False
    except NegotiationError:
        pass
    try:
        await svc.name_season("   ")
        assert False
    except NegotiationError:
        pass

    # 推进后历史赛季/窗口名保留、新赛季/新窗口名为空
    await svc.advance_season("admin1")
    assert await env.dao.get_window_name(orig_win) == "夏窗"
    assert await env.dao.get_season_name(orig_season) == "26-27"
    st = await svc.league_status()
    assert st["window_seq"] == orig_win + 1
    assert st["season_number"] == orig_season + 1
    assert st["window_name"] == ""
    assert st["season_name"] == ""


async def test_window_blocking(env: TestEnv):
    svc = env.service
    await svc.create_team("阻塞队", "admin1")
    code = await svc.generate_code("阻塞队", "admin1", hours=1)
    await svc.bind_team("20001", code["code"], "g1")
    await env.import_service.import_rows([("333", "Gamma", "")], "admin1")
    c = await svc.create_case("333", "阻塞队", 25, 80, 80, 10, "admin1")
    try:
        await svc.advance_window("admin1")
        assert False, "window advanced with active case"
    except NegotiationError:
        pass
    n = await svc.close_window_cases()
    assert n == 1
    await svc.advance_window("admin1")


async def test_contract_overwrite(env: TestEnv):
    svc = env.service
    cur_win = (await env.dao.get_league_state())["window_seq"]
    await svc.create_team("覆盖队", "admin1")
    code = await svc.generate_code("覆盖队", "admin1", hours=1)
    await svc.bind_team("21001", code["code"], "g1")
    await env.import_service.import_rows([("666", "Overwrite", "")], "admin1")
    c1 = await svc.create_case("666", "覆盖队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c1["case_id"], "21001", 14)
    s1 = await env.dao.get_session_by_case(c1["case_id"])
    await svc.offer_wage(c1["case_id"], "21001", s1["expected_wage"])
    old = await env.dao.list_contracts(cur_win, None)
    assert len(old) == 1 and old[0]["is_active"] == 1

    # 同球员同队再谈 → 新约覆盖旧约
    c2 = await svc.create_case("666", "覆盖队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c2["case_id"], "21001", 16)
    s2 = await env.dao.get_session_by_case(c2["case_id"])
    await svc.offer_wage(c2["case_id"], "21001", s2["expected_wage"])
    contracts = await env.dao.list_contracts(cur_win, None)
    assert len(contracts) == 1
    assert contracts[0]["is_active"] == 1
    assert contracts[0]["release_fee"] == 16
    old_row = await env.db.fetchone("SELECT is_active FROM contracts WHERE id=?", (old[0]["id"],))
    assert old_row["is_active"] == 0


async def test_rebind_after_unbind(env: TestEnv):
    svc = env.service
    await svc.create_team("重绑队", "admin1")
    code = await svc.generate_code("重绑队", "admin1", hours=1)
    await svc.bind_team("22001", code["code"], "g1")
    await svc.unbind_qq("22001")
    code2 = await svc.generate_code("重绑队", "admin1", hours=1)
    r = await svc.bind_team("22001", code2["code"], "g1")
    assert r["team_name"] == "重绑队"


async def test_expired_code_rejected(env: TestEnv):
    svc = env.service
    from astrbot_plugin_whleague_negotiation_system.utils.security import hash_auth_code

    await svc.create_team("过期队", "admin1")
    code = await svc.generate_code("过期队", "admin1", hours=1)
    await env.db.execute(
        "UPDATE auth_codes SET expires_at='2000-01-01 00:00:00' WHERE code_hash=?",
        (hash_auth_code(code["code"]),),
    )
    try:
        await svc.bind_team("23001", code["code"], "g1")
        assert False
    except NegotiationError as e:
        assert "过期" in str(e)


async def test_team_member_offer(env: TestEnv):
    svc = env.service
    await svc.create_team("全队队", "admin1")
    for qq in ("24001", "24002"):
        code = await svc.generate_code("全队队", "admin1", hours=1)
        await svc.bind_team(qq, code["code"], "g1")
    await env.import_service.import_rows([("777", "Teammate", "")], "admin1")
    c = await svc.create_case("777", "全队队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c["case_id"], "24001", 14)
    s = await env.dao.get_session_by_case(c["case_id"])
    r = await svc.offer_wage(c["case_id"], "24002", s["expected_wage"])
    assert r["result"] == "success"


async def test_self_heal_forced(env: TestEnv):
    svc = env.service
    await svc.create_team("自愈队", "admin1")
    code = await svc.generate_code("自愈队", "admin1", hours=1)
    await svc.bind_team("25001", code["code"], "g1")
    await env.import_service.import_rows([("888", "Heal", "")], "admin1")
    c = await svc.create_case("888", "自愈队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c["case_id"], "25001", 14)
    s = await env.dao.get_session_by_case(c["case_id"])
    # 模拟中断：3 次报价已落库但合同未生成（崩溃于自动成约前）
    await env.db.execute(
        "INSERT INTO negotiation_attempts (session_id, attempt_no, offered_wage, expected_wage, result) "
        "VALUES (?,1,0.01,?,'fail'), (?,2,0.02,?,'fail'), (?,3,0.03,?,'fail')",
        (s["id"], s["expected_wage"], s["id"], s["expected_wage"], s["id"], s["expected_wage"]),
    )
    r = await svc.offer_wage(c["case_id"], "25001", 0.05)
    assert r["result"] == "forced"
    assert r["wage"] == s["expected_wage"]
    row = await env.db.fetchone(
        "SELECT source FROM contracts WHERE session_id=?", (s["id"],)
    )
    assert row is not None and row["source"] == "forced"


async def test_direct_fail_settle(env: TestEnv):
    svc = env.service
    await svc.create_team("直接队", "admin1")
    code = await svc.generate_code("直接队", "admin1", hours=1)
    await svc.bind_team("26001", code["code"], "g1")
    await env.import_service.import_rows([("901", "Low", "")], "admin1")
    c = await svc.create_case("901", "直接队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c["case_id"], "26001", 14)
    s = await env.dao.get_session_by_case(c["case_id"])
    base = s["expected_wage"]
    eff2 = round(base * float(env.cfg["attempt_decay_multiplier"]), 2)
    await env.db.execute(
        "UPDATE player_roster SET agent_tier=3 WHERE player_uid='901'"
    )
    env.cfg["agent_tiers"] = DEFAULT_CONFIG["agent_tiers"]

    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        side_effect=[0.99, 0.99],
    ):
        r1 = await svc.offer_wage(c["case_id"], "26001", 0.01)
    assert r1["result"] == "fail"
    assert r1["risk"] is True

    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        return_value=0.1,
    ):
        r2 = await svc.offer_wage(c["case_id"], "26001", 0.02)
    assert r2["result"] == "direct"
    assert r2["attempt_no"] == 2
    assert r2["wage"] == eff2
    win = (await env.dao.get_league_state())["window_seq"]
    contract = (await env.dao.list_contracts(win, None))[0]
    assert contract["source"] == "direct"
    assert contract["wage"] == eff2
    case_row = await env.dao.get_case_by_id(c["case_id"])
    assert case_row["status"] == "success"
    sess = await env.dao.get_session_by_case(c["case_id"])
    assert sess["status"] == "success" and sess["attempt_count"] == 2
    attempts = await env.dao.count_attempts(s["id"])
    assert attempts == 2


async def test_direct_fail_last_attempt(env: TestEnv):
    svc = env.service
    await svc.create_team("末次队", "admin1")
    code = await svc.generate_code("末次队", "admin1", hours=1)
    await svc.bind_team("26002", code["code"], "g1")
    await env.import_service.import_rows([("902", "Last", "")], "admin1")
    c = await svc.create_case("902", "末次队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c["case_id"], "26002", 14)
    s = await env.dao.get_session_by_case(c["case_id"])
    base = s["expected_wage"]
    env.cfg["agent_tiers"] = DEFAULT_CONFIG["agent_tiers"]

    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        return_value=0.99,
    ):
        await svc.offer_wage(c["case_id"], "26002", 0.01)
        await svc.offer_wage(c["case_id"], "26002", 0.02)

    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        return_value=0.1,
    ):
        r3 = await svc.offer_wage(c["case_id"], "26002", 0.03)
    assert r3["result"] == "direct"
    assert r3["attempt_no"] == 3
    assert r3["wage"] == base
    win = (await env.dao.get_league_state())["window_seq"]
    contract = (await env.dao.list_contracts(win, None))[0]
    assert contract["source"] == "direct"
    assert contract["wage"] == base


async def test_reroll_agent_tiers(env: TestEnv):
    local = TestEnv()
    await local.setup()
    try:
        svc = local.service
        await local.import_service.import_rows(
            [("911", "R1", ""), ("912", "R2", ""), ("913", "R3", "")], "admin1"
        )
        env = local
        env.cfg["agent_change_probability"] = 1.0
        with mock.patch(
            "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
            return_value=0.1,
        ), mock.patch(
            "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.choice",
            return_value=3,
        ):
            await svc.advance_window("admin1")
        rows = await env.db.fetchall(
            "SELECT agent_tier FROM player_roster "
            "WHERE player_uid IN ('911','912','913') ORDER BY id"
        )
        assert [r["agent_tier"] for r in rows] == [3, 3, 3]

        env.cfg["agent_change_probability"] = 0.0
        await svc.advance_window("admin1")
        rows = await env.db.fetchall(
            "SELECT agent_tier FROM player_roster "
            "WHERE player_uid IN ('911','912','913') ORDER BY id"
        )
        assert [r["agent_tier"] for r in rows] == [3, 3, 3]

        env.cfg["agent_change_probability"] = 0.5
        with mock.patch(
            "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
            side_effect=[0.2, 0.9, 0.2],
        ), mock.patch(
            "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.choice",
            return_value=1,
        ):
            await svc.advance_window("admin1")
        rows = await env.db.fetchall(
            "SELECT agent_tier FROM player_roster "
            "WHERE player_uid IN ('911','912','913') ORDER BY id"
        )
        assert [r["agent_tier"] for r in rows] == [1, 3, 1]
    finally:
        await local.teardown()


async def test_runtime_fallback_defaults(env: TestEnv):
    svc = env.service
    await svc.create_team("回退队", "admin1")
    code = await svc.generate_code("回退队", "admin1", hours=1)
    await svc.bind_team("26003", code["code"], "g1")
    await env.import_service.import_rows([("903", "Fallback", "")], "admin1")
    c = await svc.create_case("903", "回退队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(c["case_id"], "26003", 14)
    s = await env.dao.get_session_by_case(c["case_id"])
    env.cfg["agent_tiers"] = "garbage-not-json"

    with mock.patch(
        "astrbot_plugin_whleague_negotiation_system.services.negotiation_service.random.random",
        return_value=0.1,
    ):
        r = await svc.offer_wage(c["case_id"], "26003", 0.01)
    assert r["result"] == "direct"
    assert r["wage"] == s["expected_wage"]

    env.cfg["agent_tiers"] = '{"1":{"threshold":0.9,"probability":0.3},"2":{"threshold":0.95,"probability":0.5},"3":{"threshold":0.99,"probability":0.7}}'
    env.cfg["agent_change_probability"] = 1.0
    await svc.advance_window("admin1")
    rows = await env.db.fetchall(
        "SELECT agent_tier FROM player_roster "
        "WHERE player_uid='903'"
    )
    assert rows[0]["agent_tier"] in (1, 2, 3)


def run_all():
    import asyncio

    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_full_flow(env)
            print("  PASS test_full_flow")
            await test_unbind_team(env)
            print("  PASS test_unbind_team")
            await test_window_naming(env)
            print("  PASS test_window_naming")
            await test_window_blocking(env)
            print("  PASS test_window_blocking")
            await test_contract_overwrite(env)
            print("  PASS test_contract_overwrite")
            await test_rebind_after_unbind(env)
            print("  PASS test_rebind_after_unbind")
            await test_expired_code_rejected(env)
            print("  PASS test_expired_code_rejected")
            await test_team_member_offer(env)
            print("  PASS test_team_member_offer")
            await test_self_heal_forced(env)
            print("  PASS test_self_heal_forced")
            await test_direct_fail_settle(env)
            print("  PASS test_direct_fail_settle")
            await test_direct_fail_last_attempt(env)
            print("  PASS test_direct_fail_last_attempt")
            await test_runtime_fallback_defaults(env)
            print("  PASS test_runtime_fallback_defaults")
            await test_reroll_agent_tiers(env)
            print("  PASS test_reroll_agent_tiers")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 13

