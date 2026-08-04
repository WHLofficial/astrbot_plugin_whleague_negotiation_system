from datetime import datetime


class NegotiationDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ─── teams ─────────────────────────────────────────────

    async def create_team(self, name: str, created_by: str) -> int:
        cur = await self._db.execute(
            "INSERT INTO teams (name, created_by) VALUES (?, ?)", (name, created_by)
        )
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def get_team_by_name(self, name: str):
        return await self._db.fetchone("SELECT * FROM teams WHERE name=?", (name,))

    async def get_team_by_id(self, team_id: int):
        return await self._db.fetchone("SELECT * FROM teams WHERE id=?", (team_id,))

    async def list_teams(self) -> list:
        return await self._db.fetchall("SELECT * FROM teams ORDER BY name")

    # ─── team bindings ──────────────────────────────────────

    async def get_binding_by_qq(self, qq: str):
        return await self._db.fetchone(
            "SELECT b.*, t.name AS team_name FROM team_bindings b "
            "JOIN teams t ON t.id=b.team_id WHERE b.qq=?",
            (qq,),
        )

    async def get_bindings_by_team(self, team_id: int) -> list:
        return await self._db.fetchall(
            "SELECT * FROM team_bindings WHERE team_id=? ORDER BY id", (team_id,)
        )

    async def get_binding(self, team_id: int, qq: str):
        return await self._db.fetchone(
            "SELECT * FROM team_bindings WHERE team_id=? AND qq=?", (team_id, qq)
        )

    async def remove_binding(self, qq: str) -> None:
        await self._db.execute("DELETE FROM team_bindings WHERE qq=?", (qq,))

    async def has_active_session(self, qq: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM negotiation_sessions s "
            "JOIN negotiation_cases c ON c.id=s.case_id "
            "WHERE s.qq=? AND c.status='negotiating' LIMIT 1",
            (qq,),
        )
        return row is not None

    # ─── auth codes ─────────────────────────────────────────

    async def insert_auth_code(
        self, team_id: int, code_hash: str, expires_at: str, generated_by: str
    ) -> int:
        cur = await self._db.execute(
            "INSERT INTO auth_codes (team_id, code_hash, expires_at, generated_by) "
            "VALUES (?, ?, ?, ?)",
            (team_id, code_hash, expires_at, generated_by),
        )
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def get_code_by_hash(self, code_hash: str):
        return await self._db.fetchone(
            "SELECT * FROM auth_codes WHERE code_hash=?", (code_hash,)
        )

    async def list_codes(self, team_id: int | None = None) -> list:
        if team_id is None:
            return await self._db.fetchall(
                "SELECT c.*, t.name AS team_name FROM auth_codes c "
                "JOIN teams t ON t.id=c.team_id ORDER BY c.created_at DESC"
            )
        return await self._db.fetchall(
            "SELECT c.*, t.name AS team_name FROM auth_codes c "
            "JOIN teams t ON t.id=c.team_id WHERE c.team_id=? ORDER BY c.created_at DESC",
            (team_id,),
        )

    # ─── player roster ──────────────────────────────────────

    async def upsert_player(
        self, conn, player_uid: str, foreign_name: str, chinese_name: str, created_by: str
    ) -> None:
        await conn.execute(
            "INSERT INTO player_roster (player_uid, foreign_name, chinese_name, created_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(player_uid) DO UPDATE SET "
            "foreign_name=excluded.foreign_name, chinese_name=excluded.chinese_name, "
            "created_by=excluded.created_by, "
            "updated_at=datetime('now','localtime')",
            (player_uid, foreign_name, chinese_name, created_by),
        )

    async def get_player_by_uid(self, player_uid: str):
        return await self._db.fetchone(
            "SELECT * FROM player_roster WHERE player_uid=?", (player_uid,)
        )

    async def get_player_by_id(self, player_id: int):
        return await self._db.fetchone(
            "SELECT * FROM player_roster WHERE id=?", (player_id,)
        )

    async def count_players(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) AS n FROM player_roster")
        return row["n"] if row else 0

    # ─── negotiation cases ──────────────────────────────────

    async def create_case(
        self,
        conn,
        player_id: int,
        team_id: int,
        season_number: int,
        window_seq: int,
        age: int,
        ca: int,
        pa: int,
        old_release_fee: int,
        created_by: str,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO negotiation_cases (player_id, team_id, season_number, window_seq, "
            "age, ca, pa, old_release_fee, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                player_id,
                team_id,
                season_number,
                window_seq,
                age,
                ca,
                pa,
                old_release_fee,
                created_by,
            ),
        )
        return cur.lastrowid

    async def get_case_by_id(self, case_id: int):
        return await self._db.fetchone(
            "SELECT * FROM negotiation_cases WHERE id=?", (case_id,)
        )

    async def update_case_status(self, conn, case_id: int, status: str) -> None:
        await conn.execute(
            "UPDATE negotiation_cases SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
            (status, case_id),
        )

    async def count_active_cases_in_window(self, window_seq: int) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM negotiation_cases "
            "WHERE window_seq=? AND status IN ('pending','negotiating')",
            (window_seq,),
        )
        return row["n"] if row else 0

    async def list_cases(
        self,
        window_seq: int,
        offset: int = 0,
        limit: int = 10,
        team_id: int | None = None,
        status: str | None = None,
    ) -> list:
        if team_id is None:
            if status is None:
                return await self._db.fetchall(
                    "SELECT c.*, p.player_uid, p.foreign_name, t.name AS team_name "
                    "FROM negotiation_cases c "
                    "JOIN player_roster p ON p.id=c.player_id "
                    "JOIN teams t ON t.id=c.team_id "
                    "WHERE c.window_seq=? ORDER BY c.id DESC LIMIT ? OFFSET ?",
                    (window_seq, limit, offset),
                )
            return await self._db.fetchall(
                "SELECT c.*, p.player_uid, p.foreign_name, t.name AS team_name "
                "FROM negotiation_cases c "
                "JOIN player_roster p ON p.id=c.player_id "
                "JOIN teams t ON t.id=c.team_id "
                "WHERE c.window_seq=? AND c.status=? ORDER BY c.id DESC LIMIT ? OFFSET ?",
                (window_seq, status, limit, offset),
            )
        if status is None:
            return await self._db.fetchall(
                "SELECT c.*, p.player_uid, p.foreign_name, t.name AS team_name "
                "FROM negotiation_cases c "
                "JOIN player_roster p ON p.id=c.player_id "
                "JOIN teams t ON t.id=c.team_id "
                "WHERE c.window_seq=? AND c.team_id=? ORDER BY c.id DESC LIMIT ? OFFSET ?",
                (window_seq, team_id, limit, offset),
            )
        return await self._db.fetchall(
            "SELECT c.*, p.player_uid, p.foreign_name, t.name AS team_name "
            "FROM negotiation_cases c "
            "JOIN player_roster p ON p.id=c.player_id "
            "JOIN teams t ON t.id=c.team_id "
            "WHERE c.window_seq=? AND c.team_id=? AND c.status=? "
            "ORDER BY c.id DESC LIMIT ? OFFSET ?",
            (window_seq, team_id, status, limit, offset),
        )

    async def count_cases_in_window(
        self, window_seq: int, team_id: int | None = None, status: str | None = None
    ) -> int:
        if status is not None:
            if team_id is None:
                row = await self._db.fetchone(
                    "SELECT COUNT(*) AS n FROM negotiation_cases "
                    "WHERE window_seq=? AND status=?",
                    (window_seq, status),
                )
            else:
                row = await self._db.fetchone(
                    "SELECT COUNT(*) AS n FROM negotiation_cases "
                    "WHERE window_seq=? AND team_id=? AND status=?",
                    (window_seq, team_id, status),
                )
        elif team_id is None:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM negotiation_cases WHERE window_seq=?",
                (window_seq,),
            )
        else:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM negotiation_cases WHERE window_seq=? AND team_id=?",
                (window_seq, team_id),
            )
        return row["n"] if row else 0

    async def get_latest_contract_fee(self, player_id: int) -> int | None:
        row = await self._db.fetchone(
            "SELECT release_fee FROM contracts WHERE player_id=? AND is_active=1 "
            "ORDER BY id DESC LIMIT 1",
            (player_id,),
        )
        return row["release_fee"] if row else None

    # ─── negotiation sessions ───────────────────────────────

    async def create_session(
        self, conn, case_id: int, qq: str, release_fee: int, expected_wage: float
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO negotiation_sessions (case_id, qq, release_fee, expected_wage) "
            "VALUES (?, ?, ?, ?)",
            (case_id, qq, release_fee, expected_wage),
        )
        return cur.lastrowid

    async def get_session_by_case(self, case_id: int):
        return await self._db.fetchone(
            "SELECT * FROM negotiation_sessions WHERE case_id=?", (case_id,)
        )

    async def get_session_by_id(self, session_id: int):
        return await self._db.fetchone(
            "SELECT * FROM negotiation_sessions WHERE id=?", (session_id,)
        )

    async def update_session_release_fee(
        self, conn, session_id: int, release_fee: int, expected_wage: float
    ) -> None:
        await conn.execute(
            "UPDATE negotiation_sessions SET release_fee=?, expected_wage=? WHERE id=?",
            (release_fee, expected_wage, session_id),
        )

    async def finish_session(
        self, conn, session_id: int, attempt_count: int | None = None
    ) -> None:
        if attempt_count is None:
            await conn.execute(
                "UPDATE negotiation_sessions SET status='success', "
                "finished_at=datetime('now','localtime') WHERE id=?",
                (session_id,),
            )
        else:
            await conn.execute(
                "UPDATE negotiation_sessions SET status='success', attempt_count=?, "
                "finished_at=datetime('now','localtime') WHERE id=?",
                (attempt_count, session_id),
            )

    # ─── attempts ───────────────────────────────────────────

    async def insert_attempt(
        self,
        conn,
        session_id: int,
        attempt_no: int,
        offered_wage: float,
        expected_wage: float,
        result: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO negotiation_attempts (session_id, attempt_no, offered_wage, expected_wage, result) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, attempt_no, offered_wage, expected_wage, result),
        )

    async def get_last_attempt(self, session_id: int):
        return await self._db.fetchone(
            "SELECT * FROM negotiation_attempts WHERE session_id=? "
            "ORDER BY attempt_no DESC LIMIT 1",
            (session_id,),
        )

    async def count_attempts(self, session_id: int) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM negotiation_attempts WHERE session_id=?",
            (session_id,),
        )
        return row["n"] if row else 0

    # ─── contracts ──────────────────────────────────────────

    async def deactivate_contracts(self, conn, player_id: int) -> None:
        await conn.execute(
            "UPDATE contracts SET is_active=0 WHERE player_id=? AND is_active=1",
            (player_id,),
        )

    async def insert_contract(
        self,
        conn,
        case_id: int,
        session_id: int,
        team_id: int,
        player_id: int,
        season_number: int,
        window_seq: int,
        wage: float,
        release_fee: int,
        source: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO contracts (case_id, session_id, team_id, player_id, "
            "season_number, window_seq, wage, release_fee, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                session_id,
                team_id,
                player_id,
                season_number,
                window_seq,
                wage,
                release_fee,
                source,
            ),
        )

    async def list_contracts(
        self, window_seq: int, team_id: int | None, offset: int = 0, limit: int = 10
    ) -> list:
        if team_id is None:
            return await self._db.fetchall(
                "SELECT ct.*, p.player_uid, p.foreign_name, t.name AS team_name "
                "FROM contracts ct "
                "JOIN player_roster p ON p.id=ct.player_id "
                "JOIN teams t ON t.id=ct.team_id "
                "WHERE ct.window_seq=? AND ct.is_active=1 "
                "ORDER BY ct.id DESC LIMIT ? OFFSET ?",
                (window_seq, limit, offset),
            )
        return await self._db.fetchall(
            "SELECT ct.*, p.player_uid, p.foreign_name, t.name AS team_name "
            "FROM contracts ct "
            "JOIN player_roster p ON p.id=ct.player_id "
            "JOIN teams t ON t.id=ct.team_id "
            "WHERE ct.window_seq=? AND ct.team_id=? AND ct.is_active=1 "
            "ORDER BY ct.id DESC LIMIT ? OFFSET ?",
            (window_seq, team_id, limit, offset),
        )

    async def count_contracts(self, window_seq: int, team_id: int | None) -> int:
        if team_id is None:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM contracts WHERE window_seq=? AND is_active=1",
                (window_seq,),
            )
        else:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM contracts WHERE window_seq=? AND team_id=? AND is_active=1",
                (window_seq, team_id),
            )
        return row["n"] if row else 0

    # ─── admins ─────────────────────────────────────────────

    async def is_admin(self, qq: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 FROM admins WHERE qq=? LIMIT 1", (qq,)
        )
        return row is not None

    async def add_admin(self, qq: str, added_by: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO admins (qq, added_by) VALUES (?, ?)",
            (qq, added_by),
        )

    async def remove_admin(self, qq: str) -> None:
        await self._db.execute("DELETE FROM admins WHERE qq=?", (qq,))

    async def list_admins(self) -> list:
        return await self._db.fetchall("SELECT * FROM admins ORDER BY id")

    # ─── league state ───────────────────────────────────────

    async def get_league_state(self):
        return await self._db.fetchone(
            "SELECT * FROM league_state WHERE id=1"
        )

    async def advance_window(self, conn, updated_by: str) -> None:
        await conn.execute(
            "UPDATE league_state SET window_seq=window_seq+1, "
            "updated_by=?, updated_at=datetime('now','localtime') WHERE id=1",
            (updated_by,),
        )

    async def advance_season(self, conn, updated_by: str) -> None:
        await conn.execute(
            "UPDATE league_state SET season_number=season_number+1, window_seq=window_seq+1, "
            "updated_by=?, updated_at=datetime('now','localtime') WHERE id=1",
            (updated_by,),
        )

    # ─── windows ────────────────────────────────────────────

    async def insert_window(self, conn, window_seq: int, season_number: int) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO windows (window_seq, season_number) VALUES (?, ?)",
            (window_seq, season_number),
        )

    async def rename_window(self, conn, window_seq: int, name: str) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO windows (window_seq, season_number) "
            "SELECT window_seq, season_number FROM league_state WHERE id=1"
        )
        await conn.execute(
            "UPDATE windows SET name=? WHERE window_seq=?", (name, window_seq)
        )

    async def get_window(self, window_seq: int):
        return await self._db.fetchone(
            "SELECT * FROM windows WHERE window_seq=?", (window_seq,)
        )

    async def get_window_name(self, window_seq: int) -> str:
        row = await self._db.fetchone(
            "SELECT name FROM windows WHERE window_seq=?", (window_seq,)
        )
        return row["name"] if row else ""

    # ─── seasons ────────────────────────────────────────────

    async def insert_season(self, conn, season_number: int) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO seasons (season_number) VALUES (?)",
            (season_number,),
        )

    async def rename_season(self, conn, season_number: int, name: str) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO seasons (season_number) "
            "SELECT season_number FROM league_state WHERE id=1"
        )
        await conn.execute(
            "UPDATE seasons SET name=? WHERE season_number=?", (name, season_number)
        )

    async def get_season_name(self, season_number: int) -> str:
        row = await self._db.fetchone(
            "SELECT name FROM seasons WHERE season_number=?", (season_number,)
        )
        return row["name"] if row else ""

    # ─── plugin config ──────────────────────────────────────

    async def get_all_config(self) -> list:
        return await self._db.fetchall("SELECT * FROM plugin_config")

    async def set_config(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO plugin_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, value),
        )
