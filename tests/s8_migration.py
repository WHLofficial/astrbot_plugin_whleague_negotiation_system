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

    assert row["value"] == "5"
    assert "code" not in names
    assert code_row["code_hash"] == "h1"


async def test_v2_to_v3_migration():
    env = TestEnv()
    await env.db.init()
    conn = env.db.conn
    await conn.executescript(
        """
        CREATE TABLE league_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            season_number INTEGER NOT NULL DEFAULT 1,
            window_seq INTEGER NOT NULL DEFAULT 1,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO league_state (id, season_number, window_seq) VALUES (1, 2, 5);
        CREATE TABLE plugin_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO plugin_config (key, value) VALUES ('schema_version', '2');
        """
    )
    await conn.commit()
    await env.db.close()

    db2 = DatabaseManager(env.db.db_path)
    await db2.init()
    await init_schema(db2)
    row = await db2.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
    win = await db2.fetchone("SELECT window_seq, season_number FROM windows WHERE window_seq=5")
    await db2.close()
    env._tmp.cleanup()

    assert row["value"] == "5"
    assert win is not None
    assert win["season_number"] == 2


async def test_v3_to_v4_migration():
    env = TestEnv()
    await env.db.init()
    conn = env.db.conn
    await conn.executescript(
        """
        CREATE TABLE league_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            season_number INTEGER NOT NULL DEFAULT 1,
            window_seq INTEGER NOT NULL DEFAULT 1,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO league_state (id, season_number, window_seq) VALUES (1, 3, 7);
        CREATE TABLE windows (
            window_seq INTEGER PRIMARY KEY,
            season_number INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO windows (window_seq, season_number, name) VALUES (7, 3, '旧窗');
        CREATE TABLE plugin_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO plugin_config (key, value) VALUES ('schema_version', '3');
        """
    )
    await conn.commit()
    await env.db.close()

    db2 = DatabaseManager(env.db.db_path)
    await db2.init()
    await init_schema(db2)
    row = await db2.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
    season = await db2.fetchone("SELECT name FROM seasons WHERE season_number=3")
    win_name = await db2.fetchone("SELECT name FROM windows WHERE window_seq=7")
    await db2.close()
    env._tmp.cleanup()

    assert row["value"] == "5"
    assert season is not None and season["name"] == ""
    assert win_name["name"] == "旧窗"


async def test_init_idempotent():
    env = TestEnv()
    await env.db.init()
    await init_schema(env.db)
    await init_schema(env.db)
    row = await env.db.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
    assert row["value"] == "5"
    cols = await env.db.fetchall("PRAGMA table_info(player_roster)")
    assert "agent_tier" in {r["name"] for r in cols}
    state = await env.db.fetchone("SELECT COUNT(*) AS n FROM league_state WHERE id=1")
    wins = await env.db.fetchone("SELECT COUNT(*) AS n FROM windows WHERE window_seq=1")
    seasons = await env.db.fetchone("SELECT COUNT(*) AS n FROM seasons WHERE season_number=1")
    assert state["n"] == 1 and wins["n"] == 1 and seasons["n"] == 1
    await env.teardown()


async def test_v4_to_v5_migration():
    env = TestEnv()
    await env.db.init()
    conn = env.db.conn
    await conn.executescript(
        """
        CREATE TABLE player_roster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_uid TEXT NOT NULL UNIQUE,
            foreign_name TEXT NOT NULL DEFAULT '',
            chinese_name TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO player_roster (player_uid, foreign_name) VALUES ('X1', 'X1');
        CREATE TABLE plugin_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        INSERT INTO plugin_config (key, value) VALUES ('schema_version', '4');
        """
    )
    await conn.commit()
    await env.db.close()

    db2 = DatabaseManager(env.db.db_path)
    await db2.init()
    await init_schema(db2)
    row = await db2.fetchone("SELECT value FROM plugin_config WHERE key='schema_version'")
    cols = await db2.fetchall("PRAGMA table_info(player_roster)")
    names = {r["name"] for r in cols}
    player = await db2.fetchone("SELECT agent_tier FROM player_roster WHERE player_uid='X1'")
    await db2.close()
    env._tmp.cleanup()

    assert row["value"] == "5"
    assert "agent_tier" in names
    assert player["agent_tier"] == 2


async def test_fresh_install_agent_tier_column():
    env = TestEnv()
    await env.db.init()
    await init_schema(env.db)
    cols = await env.db.fetchall("PRAGMA table_info(player_roster)")
    names = {r["name"] for r in cols}
    assert "agent_tier" in names
    await env.db.execute(
        "INSERT INTO player_roster (player_uid, foreign_name) VALUES ('N1', 'N1')"
    )
    player = await env.db.fetchone("SELECT agent_tier FROM player_roster WHERE player_uid='N1'")
    assert player["agent_tier"] == 2
    await env.teardown()


def run_all():
    async def main():
        await test_v1_to_v2_migration()
        print("  PASS test_v1_to_v2_migration")
        await test_v2_to_v3_migration()
        print("  PASS test_v2_to_v3_migration")
        await test_v3_to_v4_migration()
        print("  PASS test_v3_to_v4_migration")
        await test_init_idempotent()
        print("  PASS test_init_idempotent")
        await test_v4_to_v5_migration()
        print("  PASS test_v4_to_v5_migration")
        await test_fresh_install_agent_tier_column()
        print("  PASS test_fresh_install_agent_tier_column")

    asyncio.run(main())
    return 6
