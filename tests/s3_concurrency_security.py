"""并发与安全测试。"""

import asyncio

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.services.negotiation_service import (
    NegotiationError,
)


async def _make_case(env: TestEnv, team="并发队", uid="441", qq="30001"):
    svc = env.service
    await svc.create_team(team, "admin1")
    code = await svc.generate_code(team, "admin1", hours=1)
    await svc.bind_team(qq, code["code"], "g1")
    await env.import_service.import_rows([(uid, "Conc", "")], "admin1")
    return await svc.create_case(uid, team, 22, 88, 88, 12, "admin1")


async def test_concurrent_offers(env: TestEnv):
    svc = env.service
    case = await _make_case(env)
    await svc.start_negotiation(case["case_id"], "30001", 14)
    session = await env.dao.get_session_by_case(case["case_id"])
    target = session["expected_wage"]

    async def try_offer():
        return await svc.offer_wage(case["case_id"], "30001", target)

    results = await asyncio.gather(
        *[try_offer() for _ in range(10)], return_exceptions=True
    )
    successes = [r for r in results if isinstance(r, dict) and r["result"] == "success"]
    errors = [r for r in results if isinstance(r, NegotiationError)]
    assert len(successes) == 1, results
    assert len(successes) + len(errors) == 10

    attempts = await env.dao.count_attempts(session["id"])
    assert attempts == 1
    contracts = await env.dao.list_contracts(1, None)
    assert len(contracts) == 1


async def test_code_single_use_concurrent(env: TestEnv):
    svc = env.service
    await svc.create_team("码队", "admin1")
    code = await svc.generate_code("码队", "admin1", hours=1)

    async def try_bind(qq):
        return await svc.bind_team(qq, code["code"], "g1")

    results = await asyncio.gather(
        try_bind("40001"), try_bind("40002"), try_bind("40003"), return_exceptions=True
    )
    ok = [r for r in results if isinstance(r, dict)]
    bad = [r for r in results if isinstance(r, NegotiationError)]
    assert len(ok) == 1, results
    assert len(bad) == 2


async def test_bind_fail_lock(env: TestEnv):
    svc = env.service
    await svc.create_team("锁队", "admin1")
    env.cfg["auth_code_attempt_limit"] = 3
    env.cfg["auth_code_lock_minutes"] = 10
    for i in range(3):
        try:
            await svc.bind_team("50001", f"BADCODE{i}", "g1")
            assert False, "invalid code accepted"
        except NegotiationError:
            pass
    try:
        await svc.bind_team("50001", "BADCODE", "g1")
        assert False, "bind lock not triggered"
    except NegotiationError as e:
        assert "频繁" in str(e)


async def test_unbind_blocked_with_session(env: TestEnv):
    svc = env.service
    case = await _make_case(env, team="解锁队", uid="552", qq="60001")
    await svc.start_negotiation(case["case_id"], "60001", 14)
    try:
        await svc.unbind_qq("60001")
        assert False, "unbind allowed with active session"
    except NegotiationError:
        pass


async def test_concurrent_offers_50(env: TestEnv):
    svc = env.service
    case = await _make_case(env, team="高压队", uid="443", qq="30002")
    await svc.start_negotiation(case["case_id"], "30002", 14)
    session = await env.dao.get_session_by_case(case["case_id"])
    target = session["expected_wage"]

    async def try_offer():
        return await svc.offer_wage(case["case_id"], "30002", target)

    results = await asyncio.gather(
        *[try_offer() for _ in range(50)], return_exceptions=True
    )
    successes = [r for r in results if isinstance(r, dict) and r["result"] == "success"]
    errors = [r for r in results if isinstance(r, NegotiationError)]
    assert len(successes) == 1, results
    assert len(successes) + len(errors) == 50
    row = await env.db.fetchone(
        "SELECT COUNT(*) AS n FROM contracts WHERE session_id=?", (session["id"],)
    )
    assert row["n"] == 1


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_concurrent_offers(env)
            print("  PASS test_concurrent_offers")
            await test_code_single_use_concurrent(env)
            print("  PASS test_code_single_use_concurrent")
            await test_bind_fail_lock(env)
            print("  PASS test_bind_fail_lock")
            await test_unbind_blocked_with_session(env)
            print("  PASS test_unbind_blocked_with_session")
            await test_concurrent_offers_50(env)
            print("  PASS test_concurrent_offers_50")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 5
