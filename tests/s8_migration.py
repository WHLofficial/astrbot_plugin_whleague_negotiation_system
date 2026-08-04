"""schema 迁移测试：v1（含明文 code 列）→ v2（仅哈希）。"""

import asyncio

from .common import TestEnv

from astrbot_plugin_whleague_negotiation_system.db.connection import DatabaseManager
from astrbot_plugin_whleague_negotiation_system.db.schema import init_schema


async def test_v1_to_v2_migration():
    env = TestEnv()
    await env.db.init()
    conn = env.db.conn
    await conn.executescript(
        """
        CREATE TABLE auth_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            code TEXT NOT NULL UNIQUE,
            code_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            generated_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO auth_codes (team_id, code, code_hash, expires_at)
        VALUES (1, 'PLAINTEXT', 'h1', '2099-01-01 00:00:00');
        CREATE TABLE plugin_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO plugin_config (key, value) VALUES ('schema_version', '1');
        """
    )
    await conn.commit()
    await env.db.close()

    db2 = DatabaseManager(env.db.db_path)
    await db2.init()
    await init_schema(db2)
    row = await db2.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
    cols = await db2.fetchall("PRAGMA table_info(auth_codes)")
    names = {r["name"] for r in cols}
    code_row = await db2.fetchone("SELECT code_hash FROM auth_codes WHERE id=1")
    await db2.close()
    env._tmp.cleanup()

    assert row["value"] == "2"
    assert "code" not in names
    assert code_row["code_hash"] == "h1"


def run_all():
    async def main():
        await test_v1_to_v2_migration()
        print("  PASS test_v1_to_v2_migration")

    asyncio.run(main())
    return 1
