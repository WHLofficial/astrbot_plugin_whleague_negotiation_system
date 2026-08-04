"""竞态不变量测试：窗口推进与 建案例/开始谈判/取消 并发。"""

import asyncio

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.services.negotiation_service import (
    NegotiationError,
)


async def _setup_case(env: TestEnv, team="竞态队", uid="771", qq="70001"):
    svc = env.service
    await svc.create_team(team, "admin1")
    code = await svc.generate_code(team, "admin1", hours=1)
    await svc.bind_team(qq, code["code"], "g1")
    await env.import_service.import_rows([(uid, "Race", "")], "admin1")
    return await svc.create_case(uid, team, 22, 88, 88, 12, "admin1")


async def _assert_no_stranded(env: TestEnv, expect_advance: bool = False):
    state = await env.dao.get_league_state()
    rows = await env.db.fetchall(
        "SELECT id, status, window_seq FROM negotiation_cases "
        "WHERE status IN ('pending','negotiating')"
    )
    for r in rows:
        assert r["window_seq"] == state["window_seq"], (r, state)
    if expect_advance:
        active = await env.dao.count_active_cases_in_window(state["window_seq"])
        assert active == 0


async def test_advance_vs_start(env: TestEnv):
    svc = env.service
    case = await _setup_case(env)
    case_id = case["case_id"]
    before = await env.dao.get_league_state()

    async def adv():
        try:
            return await svc.advance_window("admin1")
        except NegotiationError as e:
            return e

    async def start():
        try:
            return await svc.start_negotiation(case_id, "70001", 14)
        except NegotiationError as e:
            return e

    for _ in range(5):
        r1, r2 = await asyncio.gather(adv(), start(), return_exceptions=True)
        after = await env.dao.get_league_state()
        await _assert_no_stranded(env, expect_advance=after["window_seq"] > before["window_seq"])

    state = await env.dao.get_league_state()
    if state["window_seq"] > before["window_seq"]:
        # 推进成功 → 案例必须仍在当前窗口且未进入 negotiating
        case_row = await env.dao.get_case_by_id(case_id)
        assert case_row["window_seq"] == state["window_seq"]
        assert case_row["status"] in ("pending", "cancelled")


async def test_advance_vs_create(env: TestEnv):
    svc = env.service
    before = await env.dao.get_league_state()

    async def adv():
        try:
            return await svc.advance_window("admin1")
        except NegotiationError as e:
            return e

    async def create():
        try:
            return await svc.create_case("771", "竞态队", 22, 88, 88, 12, "admin1")
        except NegotiationError as e:
            return e

    for _ in range(5):
        await asyncio.gather(adv(), create(), return_exceptions=True)
        after = await env.dao.get_league_state()
        await _assert_no_stranded(env, expect_advance=after["window_seq"] > before["window_seq"])


async def test_cancel_vs_offer(env: TestEnv):
    svc = env.service
    case = await _setup_case(env, team="取消队", uid="881", qq="70002")
    case_id = case["case_id"]
    await svc.start_negotiation(case_id, "70002", 14)
    session = await env.dao.get_session_by_case(case_id)
    target = session["expected_wage"]

    async def cancel():
        try:
            return await svc.cancel_case(case_id)
        except NegotiationError as e:
            return e

    async def offer():
        try:
            return await svc.offer_wage(case_id, "70002", target)
        except NegotiationError as e:
            return e

    for _ in range(5):
        await asyncio.gather(cancel(), offer(), return_exceptions=True)
        contracts = await env.dao.list_contracts(1, None)
        case_row = await env.dao.get_case_by_id(case_id)
        if contracts:
            assert case_row["status"] == "success", (contracts, case_row)
        else:
            assert case_row["status"] == "cancelled", (contracts, case_row)
        # 清理后重试下一轮
        await env.db.execute("DELETE FROM contracts")
        await env.db.execute("UPDATE negotiation_cases SET status='negotiating' WHERE id=?", (case_id,))
        await env.db.execute(
            "UPDATE negotiation_sessions SET status='negotiating', finished_at=NULL WHERE id=?",
            (session["id"],),
        )


async def test_double_advance_with_create(env: TestEnv):
    svc = env.service

    async def adv():
        try:
            return await svc.advance_window("admin1")
        except NegotiationError as e:
            return e

    async def create(uid: str):
        try:
            return await svc.create_case(uid, "竞态队", 22, 88, 88, 12, "admin1")
        except NegotiationError as e:
            return e

    for i in range(5):
        await env.import_service.import_rows([(f"79{i}", "Race2", "")], "admin1")
        await asyncio.gather(
            adv(), adv(), create(f"79{i}"), return_exceptions=True
        )
        await _assert_no_stranded(env)


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_advance_vs_start(env)
            print("  PASS test_advance_vs_start")
            await test_advance_vs_create(env)
            print("  PASS test_advance_vs_create")
            await test_cancel_vs_offer(env)
            print("  PASS test_cancel_vs_offer")
            await test_double_advance_with_create(env)
            print("  PASS test_double_advance_with_create")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 4
