"""核心业务流程端到端测试。"""

import asyncio

from .common import TestEnv

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

    # 生成认证码并绑定
    code_info = await svc.generate_code("测试队", "admin1", hours=1)
    assert len(code_info["code"]) == 8
    await svc.bind_team("10001", "测试队", code_info["code"], "g1")

    # 一QQ一队：重复绑定被拒
    code2 = await svc.generate_code("测试队", "admin1", hours=1)
    try:
        await svc.bind_team("10001", "测试队", code2["code"], "g1")
        assert False, "double bind allowed"
    except NegotiationError:
        pass

    # 无效码 / 其他队码
    try:
        await svc.bind_team("10002", "测试队", "AAAA0000", "g1")
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

    # 解绑
    un = await svc.unbind("10001")
    assert un["qq"] == "10001"
    try:
        await svc.unbind("10001")
        assert False
    except NegotiationError:
        pass


async def test_window_blocking(env: TestEnv):
    svc = env.service
    await svc.create_team("阻塞队", "admin1")
    code = await svc.generate_code("阻塞队", "admin1", hours=1)
    await svc.bind_team("20001", "阻塞队", code["code"], "g1")
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


def run_all():
    import asyncio

    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_full_flow(env)
            print("  PASS test_full_flow")
            await test_window_blocking(env)
            print("  PASS test_window_blocking")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 2
