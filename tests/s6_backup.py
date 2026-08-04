"""备份服务测试。"""

import asyncio

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.services.backup_service import (
    BackupService,
)


async def test_backup_create(env: TestEnv):
    svc = env.service
    await svc.create_team("备份队", "admin1")
    bs = BackupService(env.db, env.cfg)
    result = await bs.run_backup()
    assert result["path"]
    files = list(bs.backup_dir.glob("negotiation_*.db"))
    assert len(files) == 1
    assert files[0].stat().st_size > 0


async def test_backup_keep_count(env: TestEnv):
    bs = BackupService(env.db, env.cfg)
    env.cfg["backup_keep_count"] = 2
    for _ in range(4):
        await bs.run_backup()
    files = list(bs.backup_dir.glob("negotiation_*.db"))
    assert len(files) == 2


def run_all():
    async def main():
        env = TestEnv()
        await env.setup()
        try:
            await test_backup_create(env)
            print("  PASS test_backup_create")
            await test_backup_keep_count(env)
            print("  PASS test_backup_keep_count")
        finally:
            await env.teardown()

    asyncio.run(main())
    return 2
