"""性能烟测：大导入与高并发报价（宽松阈值，防回归性挂起）。"""

import asyncio
import time

from astrbot_plugin_whleague_negotiation_system.services.negotiation_service import (
    NegotiationError,
)

from .common import TestEnv


async def test_import_10k_rows(env: TestEnv):
    data = [(f"P{i:05d}", f"Player{i}", "") for i in range(10000)]
    t0 = time.perf_counter()
    stats = await env.import_service.import_rows(data, "admin1")
    elapsed = time.perf_counter() - t0
    assert stats["added"] == 10000
    assert elapsed < 60.0, f"import too slow: {elapsed:.2f}s"
    count = await env.dao.count_players()
    assert count == 10000


async def test_concurrent_offers_50_throughput(env: TestEnv):
    svc = env.service
    await svc.create_team("性能队", "admin1")
    code = await svc.generate_code("性能队", "admin1", hours=1)
    await svc.bind_team("95001", code["code"], "g1")
    await env.import_service.import_rows([("999", "Perf", "")], "admin1")
    case = await svc.create_case("999", "性能队", 22, 88, 88, 12, "admin1")
    await svc.start_negotiation(case["case_id"], "95001", 14)
    session = await env.dao.get_session_by_case(case["case_id"])
    target = session["expected_wage"]

    async def try_offer():
        return await svc.offer_wage(case["case_id"], "95001", target)

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[try_offer() for _ in range(50)], return_exceptions=True
    )
    elapsed = time.perf_counter() - t0
    successes = [r for r in results if isinstance(r, dict) and r["result"] == "success"]
    assert len(successes) == 1
    assert elapsed < 30.0, f"50 concurrent offers too slow: {elapsed:.2f}s"


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_import_10k_rows(env)
            print("  PASS test_import_10k_rows")
            await test_concurrent_offers_50_throughput(env)
            print("  PASS test_concurrent_offers_50_throughput")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 2
