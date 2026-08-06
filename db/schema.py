from astrbot.api import logger

SCHEMA_VERSION = 5

SQL_CREATE_TABLES = r"""

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS team_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    qq TEXT NOT NULL UNIQUE,
    auth_code_id INTEGER REFERENCES auth_codes(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(team_id, qq)
);

CREATE INDEX IF NOT EXISTS idx_bindings_qq ON team_bindings(qq);

CREATE TABLE IF NOT EXISTS auth_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    code_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    generated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS player_roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uid TEXT NOT NULL UNIQUE,
    foreign_name TEXT NOT NULL DEFAULT '',
    chinese_name TEXT NOT NULL DEFAULT '',
    agent_tier INTEGER NOT NULL DEFAULT 2,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS negotiation_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES player_roster(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    season_number INTEGER NOT NULL,
    window_seq INTEGER NOT NULL,
    age INTEGER NOT NULL,
    ca INTEGER NOT NULL,
    pa INTEGER NOT NULL,
    old_release_fee INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_active_unique
    ON negotiation_cases(player_id, team_id) WHERE status IN ('pending','negotiating');
CREATE INDEX IF NOT EXISTS idx_cases_window_status ON negotiation_cases(window_seq, status);
CREATE INDEX IF NOT EXISTS idx_cases_player ON negotiation_cases(player_id);
CREATE INDEX IF NOT EXISTS idx_cases_team ON negotiation_cases(team_id);

CREATE TABLE IF NOT EXISTS negotiation_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL UNIQUE REFERENCES negotiation_cases(id),
    qq TEXT NOT NULL,
    release_fee INTEGER NOT NULL,
    expected_wage REAL NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'negotiating',
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_qq ON negotiation_sessions(qq);

CREATE TABLE IF NOT EXISTS negotiation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES negotiation_sessions(id),
    attempt_no INTEGER NOT NULL,
    offered_wage REAL NOT NULL,
    expected_wage REAL NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(session_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES negotiation_cases(id),
    session_id INTEGER NOT NULL REFERENCES negotiation_sessions(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    player_id INTEGER NOT NULL REFERENCES player_roster(id),
    season_number INTEGER NOT NULL,
    window_seq INTEGER NOT NULL,
    wage REAL NOT NULL,
    release_fee INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'negotiation',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_contracts_player_active ON contracts(player_id, is_active);
CREATE INDEX IF NOT EXISTS idx_contracts_window ON contracts(window_seq);
CREATE INDEX IF NOT EXISTS idx_contracts_team ON contracts(team_id);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qq TEXT NOT NULL UNIQUE,
    added_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS league_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    season_number INTEGER NOT NULL DEFAULT 1,
    window_seq INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS windows (
    window_seq INTEGER PRIMARY KEY,
    season_number INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS seasons (
    season_number INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS plugin_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

"""


async def init_schema(db_manager):
    db = db_manager.conn
    async with db_manager.lock:
        await db.executescript(SQL_CREATE_TABLES)
        await db.commit()

    cur = await db.execute("SELECT value FROM plugin_config WHERE key='schema_version'")
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        await db.execute(
            "INSERT INTO plugin_config (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await db.execute(
            "INSERT OR IGNORE INTO league_state (id, season_number, window_seq) VALUES (1, 1, 1)"
        )
        await db.execute(
            "INSERT OR IGNORE INTO windows (window_seq, season_number) VALUES (1, 1)"
        )
        await db.execute(
            "INSERT OR IGNORE INTO seasons (season_number) VALUES (1)"
        )
        await db.commit()
        logger.info("Database schema initialized (version %d).", SCHEMA_VERSION)
    else:
        try:
            current = int(row["value"])
        except (ValueError, TypeError):
            logger.warning(
                "Invalid schema_version value %r, rewriting to %d.",
                row["value"],
                SCHEMA_VERSION,
            )
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            current = SCHEMA_VERSION
        if current < SCHEMA_VERSION:
            await _migrate(db, current)
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            logger.info("Database schema migrated %d -> %d.", current, SCHEMA_VERSION)


async def _table_columns(db, table: str) -> set:
    cur = await db.execute(f"PRAGMA table_info({table})")
    try:
        rows = await cur.fetchall()
        return {r["name"] for r in rows}
    finally:
        await cur.close()


async def _migrate(db, current_version: int):
    """增量迁移：仅在目标列缺失时执行，保证可重复运行。"""
    if current_version < 5:
        cols = await _table_columns(db, "player_roster")
        if "agent_tier" not in cols:
            await db.execute(
                "ALTER TABLE player_roster ADD COLUMN agent_tier INTEGER NOT NULL DEFAULT 2"
            )
        await db.commit()

    if current_version < 4:
        await db.execute(
            "INSERT OR IGNORE INTO seasons (season_number) "
            "SELECT season_number FROM league_state WHERE id=1"
        )
        await db.commit()

    if current_version < 3:
        await db.execute(
            "INSERT OR IGNORE INTO windows (window_seq, season_number) "
            "SELECT window_seq, season_number FROM league_state WHERE id=1"
        )
        await db.commit()

    if current_version < 2:
        cols = await _table_columns(db, "auth_codes")
        if "code" in cols:
            # UNIQUE(code) 的自动索引无法单独 DROP，需整表重建；
            # 关闭外键约束以允许删除被 team_bindings 引用的旧表
            await db.execute("PRAGMA foreign_keys=OFF")
            await db.execute(
                "CREATE TABLE auth_codes_new ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " team_id INTEGER NOT NULL REFERENCES teams(id),"
                " code_hash TEXT NOT NULL UNIQUE,"
                " expires_at TEXT NOT NULL,"
                " used_count INTEGER NOT NULL DEFAULT 0,"
                " is_active INTEGER NOT NULL DEFAULT 1,"
                " generated_by TEXT NOT NULL DEFAULT '',"
                " created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))"
                ")"
            )
            await db.execute(
                "INSERT INTO auth_codes_new (id, team_id, code_hash, expires_at, "
                "used_count, is_active, generated_by, created_at) "
                "SELECT id, team_id, code_hash, expires_at, used_count, is_active, "
                "generated_by, created_at FROM auth_codes"
            )
            await db.execute("DROP TABLE auth_codes")
            await db.execute("ALTER TABLE auth_codes_new RENAME TO auth_codes")
            await db.execute("PRAGMA foreign_keys=ON")
        await db.commit()
