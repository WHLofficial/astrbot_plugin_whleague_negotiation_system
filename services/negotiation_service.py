"""谈判核心业务服务。

职责：球队/认证码/绑定、谈判案例与会话状态机、报价与成约（事务原子）、
赛季与转会窗口推进、列表查询。所有业务错误以 NegotiationError 抛出，
由上层 handler 转为提示文案。
"""

import random
import time
from datetime import datetime, timedelta

from astrbot.api import logger

from .formula import (
    ability_level,
    attempt_expected,
    expected_wage,
    release_fee_bounds,
    success_rate,
    success_tier,
)

_PAGE_SIZE = 10

_BIND_FAIL_WINDOW = 600.0
_BIND_FAIL_PRUNE_THRESHOLD = 2048


class NegotiationError(Exception):
    """谈判业务错误。"""


class NegotiationService:
    def __init__(self, db, dao, cfg, rate_limiter, persist_cfg=None):
        self._db = db
        self._dao = dao
        self._cfg = cfg
        self._limiter = rate_limiter
        self._persist_cfg = persist_cfg
        self._bind_fails: dict[str, list] = {}

    # ─── helpers ────────────────────────────────────────────

    def _prune_bind_fails(self, now: float) -> None:
        if len(self._bind_fails) <= _BIND_FAIL_PRUNE_THRESHOLD:
            return
        cutoff = now - _BIND_FAIL_WINDOW
        stale = [
            qq
            for qq, f in self._bind_fails.items()
            if f[1] <= now and f[2] < cutoff
        ]
        for qq in stale:
            del self._bind_fails[qq]

    @staticmethod
    def _page_params(raw: str | None, page_size: int = _PAGE_SIZE) -> tuple[int, int]:
        page = 1
        if raw and str(raw).strip().isdigit():
            page = max(1, int(str(raw).strip()))
        return page, (page - 1) * page_size

    def _tier_thresholds(self) -> tuple[float, float, float]:
        raw = str(self._cfg.get("tier_thresholds", "0.25,0.6,0.9"))
        try:
            parts = [float(p) for p in raw.split(",")]
            if len(parts) == 3 and 0 < parts[0] < parts[1] < parts[2] < 1:
                return (parts[0], parts[1], parts[2])
        except ValueError:
            pass
        return (0.25, 0.6, 0.9)

    # ─── teams ──────────────────────────────────────────────

    async def create_team(self, name: str, created_by: str) -> dict:
        from ..utils.security import sanitize_text

        name = sanitize_text(name)
        if not name:
            raise NegotiationError("队名不能为空")
        existing = await self._dao.get_team_by_name(name)
        if existing:
            raise NegotiationError(f"球队「{name}」已存在")
        team_id = await self._dao.create_team(name, created_by)
        return {"id": team_id, "name": name}

    # ─── auth codes ─────────────────────────────────────────

    async def generate_code(self, team_name: str, generated_by: str, hours: int | None) -> dict:
        from ..utils.security import generate_auth_code, hash_auth_code

        team = await self._dao.get_team_by_name(team_name)
        if not team:
            raise NegotiationError(f"球队「{team_name}」不存在")
        if hours is None:
            hours = int(self._cfg.get("auth_code_default_expire_hours", 24))
        if hours <= 0:
            raise NegotiationError("认证码时长必须大于 0 小时")
        code = generate_auth_code()
        expires_at = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        await self._dao.insert_auth_code(
            team["id"], hash_auth_code(code), expires_at, generated_by
        )
        return {"code": code, "team_name": team["name"], "expires_at": expires_at}

    async def list_codes(self, team_name: str | None) -> list:
        if team_name:
            team = await self._dao.get_team_by_name(team_name)
            if not team:
                raise NegotiationError(f"球队「{team_name}」不存在")
            return await self._dao.list_codes(team["id"])
        return await self._dao.list_codes()

    # ─── binding ────────────────────────────────────────────

    async def bind_team(self, qq: str, code: str, group_id: str | None) -> dict:
        from ..utils.security import hash_auth_code

        now = time.time()
        self._prune_bind_fails(now)
        fails, locked_until, _ = self._bind_fails.get(qq, [0, 0.0, now])
        if locked_until > now:
            minutes = max(1, int((locked_until - now) // 60 + 1))
            raise NegotiationError(f"绑定尝试过于频繁，请 {minutes} 分钟后再试")

        code_row = await self._dao.get_code_by_hash(hash_auth_code(code.strip()))
        if not code_row:
            self._register_bind_fail(qq, now)
            raise NegotiationError("认证码无效")
        if not code_row["is_active"] or code_row["used_count"] > 0:
            self._register_bind_fail(qq, now)
            raise NegotiationError("认证码已被使用或失效")
        expires = code_row["expires_at"]
        if expires and datetime.strptime(expires, "%Y-%m-%d %H:%M:%S") <= datetime.now():
            self._register_bind_fail(qq, now)
            raise NegotiationError("认证码已过期")

        team = await self._dao.get_team_by_id(code_row["team_id"])
        if not team:
            raise NegotiationError("认证码所属球队不存在")
        existing = await self._dao.get_binding_by_qq(qq)
        if existing:
            raise NegotiationError(f"你已绑定球队「{existing['team_name']}」，一QQ仅可绑定一队")

        async def _tx(conn):
            fresh = await _Tx.code_by_hash(conn, code_row["code_hash"])
            if not fresh or not fresh["is_active"] or fresh["used_count"] > 0:
                raise NegotiationError("认证码已被使用或失效")
            async with conn.execute(
                "SELECT 1 FROM team_bindings WHERE qq=? LIMIT 1", (qq,)
            ) as cur:
                if await cur.fetchone():
                    raise NegotiationError("你已绑定球队，一QQ仅可绑定一队")
            await conn.execute(
                "UPDATE auth_codes SET used_count=1, is_active=0 WHERE id=?",
                (fresh["id"],),
            )
            await conn.execute(
                "INSERT INTO team_bindings (team_id, qq, auth_code_id) VALUES (?, ?, ?)",
                (team["id"], qq, fresh["id"]),
            )
            return fresh["id"]

        try:
            await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception:
            raise NegotiationError("绑定失败，请稍后再试")

        self._bind_fails.pop(qq, None)
        return {"team_name": team["name"]}

    def _register_bind_fail(self, qq: str, now: float) -> None:
        fails, locked_until, _ = self._bind_fails.get(qq, [0, 0.0, now])
        fails += 1
        limit = int(self._cfg.get("auth_code_attempt_limit", 5))
        if fails >= limit:
            lock_min = int(self._cfg.get("auth_code_lock_minutes", 10))
            locked_until = now + lock_min * 60
            fails = 0
        self._bind_fails[qq] = [fails, locked_until, now]

    async def unbind_qq(self, qq: str) -> dict:
        binding = await self._dao.get_binding_by_qq(qq)
        if not binding:
            raise NegotiationError(f"QQ {qq} 未绑定任何球队")

        async def _tx(conn):
            async with conn.execute(
                "SELECT 1 FROM negotiation_sessions s "
                "JOIN negotiation_cases c ON c.id=s.case_id "
                "WHERE s.qq=? AND c.status='negotiating' LIMIT 1",
                (qq,),
            ) as cur:
                if await cur.fetchone():
                    raise NegotiationError("该玩家持有进行中的谈判会话，禁止解绑")
            await conn.execute("DELETE FROM team_bindings WHERE qq=?", (qq,))

        try:
            await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception:
            raise NegotiationError("解绑失败，请稍后再试")
        return {"team_name": binding["team_name"], "qq": qq}

    async def unbind_team(self, team_name: str) -> dict:
        """按队名解绑：仅当该队恰好 1 人绑定时直接解绑，否则警告要求指定 QQ。"""
        team = await self._dao.get_team_by_name(team_name)
        if not team:
            raise NegotiationError(f"球队「{team_name}」不存在")
        bindings = await self._dao.get_bindings_by_team(team["id"])
        if not bindings:
            raise NegotiationError("该队暂无绑定成员")
        if len(bindings) >= 2:
            raise NegotiationError(
                f"该队有 {len(bindings)} 人绑定，请指定要解绑的 QQ"
            )
        qq = bindings[0]["qq"]

        async def _tx(conn):
            async with conn.execute(
                "SELECT 1 FROM negotiation_sessions s "
                "JOIN negotiation_cases c ON c.id=s.case_id "
                "WHERE s.qq=? AND c.status='negotiating' LIMIT 1",
                (qq,),
            ) as cur:
                if await cur.fetchone():
                    raise NegotiationError("该玩家持有进行中的谈判会话，禁止解绑")
            await conn.execute("DELETE FROM team_bindings WHERE qq=?", (qq,))

        try:
            await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception:
            raise NegotiationError("解绑失败，请稍后再试")
        return {"team_name": team["name"], "qq": qq}

    # ─── cases ──────────────────────────────────────────────

    async def create_case(
        self, player_uid: str, team_name: str, age: int, ca: int, pa: int,
        old_fee: int, created_by: str,
    ) -> dict:
        cfg = self._cfg
        if not (cfg["age_min"] <= age <= cfg["age_max"]):
            raise NegotiationError(f"年龄需在 {cfg['age_min']}~{cfg['age_max']} 之间")
        if not (1 <= ca <= cfg["ca_max"] and 1 <= pa <= cfg["ca_max"]):
            raise NegotiationError(f"CA/PA 需在 1~{cfg['ca_max']} 之间")
        if not (1 <= old_fee <= cfg["fee_max"]):
            raise NegotiationError(f"旧违约金需在 1~{cfg['fee_max']} 之间")

        player = await self._dao.get_player_by_uid(player_uid)
        if not player:
            raise NegotiationError(f"球员库中不存在 ID「{player_uid}」，请先导入球员库")
        team = await self._dao.get_team_by_name(team_name)
        if not team:
            raise NegotiationError(f"球队「{team_name}」不存在")

        async def _tx(conn):
            state = await _Tx.league_state(conn)
            if not state:
                raise NegotiationError("赛季状态未初始化")
            try:
                case_id = await self._dao.create_case(
                    conn,
                    player["id"],
                    team["id"],
                    state["season_number"],
                    state["window_seq"],
                    age,
                    ca,
                    pa,
                    old_fee,
                    created_by,
                )
            except Exception as e:
                if "idx_cases_active_unique" in str(e) or "UNIQUE" in str(e):
                    raise NegotiationError(
                        f"球员「{player['foreign_name']}」与该球队已有进行中的谈判案例"
                    )
                raise
            return case_id, state["season_number"], state["window_seq"]

        try:
            case_id, season_number, window_seq = await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception:
            raise NegotiationError("创建案例失败，请稍后再试")

        ref_fee = await self._dao.get_latest_contract_fee(player["id"])
        return {
            "case_id": case_id,
            "player": player["foreign_name"],
            "team": team["name"],
            "ref_fee": ref_fee,
            "window_seq": window_seq,
            "season_number": season_number,
        }

    async def cancel_case(self, case_id: int) -> dict:
        case = await self._dao.get_case_by_id(case_id)
        if not case:
            raise NegotiationError(f"案例 {case_id} 不存在")
        if case["status"] not in ("pending", "negotiating"):
            raise NegotiationError(f"案例 {case_id} 当前状态不可取消")

        async def _tx(conn):
            async with conn.execute(
                "UPDATE negotiation_cases SET status='cancelled', "
                "updated_at=datetime('now','localtime') "
                "WHERE id=? AND status IN ('pending','negotiating')",
                (case_id,),
            ) as cur:
                if cur.rowcount == 0:
                    raise NegotiationError(f"案例 {case_id} 状态已变化，请刷新后重试")
            await conn.execute(
                "UPDATE negotiation_sessions SET status='cancelled', "
                "finished_at=datetime('now','localtime') "
                "WHERE case_id=? AND status='negotiating'",
                (case_id,),
            )

        await self._db.execute_transaction(_tx)
        return {"case_id": case_id}

    # ─── negotiation ────────────────────────────────────────

    async def _require_bound_team(self, qq: str, case_id: int):
        case = await self._dao.get_case_by_id(case_id)
        if not case:
            raise NegotiationError(f"案例 {case_id} 不存在")
        binding = await self._dao.get_binding_by_qq(qq)
        if not binding:
            raise NegotiationError("你尚未绑定球队，无法谈判")
        if binding["team_id"] != case["team_id"]:
            raise NegotiationError("该案例不属于你的球队")
        state = await self._dao.get_league_state()
        if not state or case["window_seq"] != state["window_seq"]:
            raise NegotiationError("该案例不属于当前转会窗口")
        return case

    async def start_negotiation(self, case_id: int, qq: str, release_fee: int) -> dict:
        case = await self._require_bound_team(qq, case_id)
        if case["status"] not in ("pending", "negotiating"):
            raise NegotiationError(f"案例 {case_id} 当前状态不可谈判")

        low, high = release_fee_bounds(case["old_release_fee"])
        if not (low <= release_fee <= high):
            raise NegotiationError(f"新违约金需在 {low}~{high} 之间（整数 M）")

        cfg = self._cfg
        level = ability_level(case["ca"], case["pa"], case["age"], int(cfg["growth_age"]))
        ew = expected_wage(
            level, release_fee, cfg["wage_param_a"], cfg["wage_param_b"], cfg["wage_param_c"]
        )

        async def _tx(conn):
            fresh_case = await _Tx.case_by_id(conn, case_id)
            state = await _Tx.league_state(conn)
            if not fresh_case or fresh_case["window_seq"] != state["window_seq"]:
                raise NegotiationError("该案例不属于当前转会窗口")
            if fresh_case["status"] not in ("pending", "negotiating"):
                raise NegotiationError(f"案例 {case_id} 当前状态不可谈判")
            session = await _Tx.session_by_case(conn, case_id)
            if session is None:
                await self._dao.create_session(conn, case_id, qq, release_fee, ew)
                await self._dao.update_case_status(conn, case_id, "negotiating")
            else:
                await self._dao.update_session_release_fee(conn, session["id"], release_fee, ew)

        try:
            await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception as e:
            if "UNIQUE" in str(e) or "session" in str(e).lower():
                raise NegotiationError("该案例正在被其他成员操作，请稍后重试")
            raise
        return {"case_id": case_id, "expected_wage": ew, "release_fee": release_fee}

    async def offer_wage(self, case_id: int, qq: str, wage: float) -> dict:
        case = await self._require_bound_team(qq, case_id)
        cfg = self._cfg
        if case["status"] != "negotiating":
            raise NegotiationError(f"案例 {case_id} 不在谈判中")
        if wage < cfg["wage_min"] or wage > cfg["wage_max"]:
            raise NegotiationError(f"报价需在 {cfg['wage_min']}~{cfg['wage_max']} 之间")

        session = await self._dao.get_session_by_case(case_id)
        if not session:
            raise NegotiationError("该案例尚未开始谈判，请先设置违约金")

        max_attempts = int(cfg["negotiation_max_attempts"])
        mult = float(cfg["attempt_decay_multiplier"])

        async def _tx(conn):
            fresh_case = await _Tx.case_by_id(conn, case_id)
            state = await _Tx.league_state(conn)
            if not fresh_case or fresh_case["window_seq"] != state["window_seq"]:
                raise NegotiationError("该案例不属于当前转会窗口")
            if fresh_case["status"] != "negotiating":
                raise NegotiationError(f"案例 {case_id} 不在谈判中")

            fresh = await _Tx.session_by_case(conn, case_id)
            count = await _Tx.count_attempts(conn, fresh["id"])

            # 幂等自愈：次数已满但尚无合同 → 补执行自动成约
            if count >= max_attempts:
                contract = await _Tx.contract_by_session(conn, fresh["id"])
                if contract:
                    raise NegotiationError("该会话已结束")
                await self._finalize(conn, fresh, fresh["expected_wage"], "forced", count)
                return {
                    "result": "forced",
                    "wage": fresh["expected_wage"],
                    "attempt_no": count,
                }

            last = await _Tx.last_attempt(conn, fresh["id"])
            if last and wage <= last["offered_wage"]:
                raise NegotiationError("报价必须高于上一次报价")

            attempt_no = count + 1
            eff_expected = attempt_expected(fresh["expected_wage"], attempt_no, mult)
            p = success_rate(wage, eff_expected)
            success = random.random() < p
            await self._dao.insert_attempt(
                conn, fresh["id"], attempt_no, wage, eff_expected,
                "success" if success else "fail",
            )
            await conn.execute(
                "UPDATE negotiation_sessions SET attempt_count=? WHERE id=?",
                (attempt_no, fresh["id"]),
            )
            if success:
                await self._finalize(conn, fresh, wage, "negotiation", attempt_no)
                return {
                    "result": "success",
                    "wage": wage,
                    "attempt_no": attempt_no,
                    "remaining": 0,
                }
            if attempt_no >= max_attempts:
                await self._finalize(conn, fresh, fresh["expected_wage"], "forced", attempt_no)
                return {
                    "result": "forced",
                    "wage": fresh["expected_wage"],
                    "attempt_no": attempt_no,
                }
            return {
                "result": "fail",
                "attempt_no": attempt_no,
                "remaining": max_attempts - attempt_no,
                "tier": success_tier(p, self._tier_thresholds()),
            }

        try:
            return await self._db.execute_transaction(_tx)
        except NegotiationError:
            raise
        except Exception:
            raise NegotiationError("报价失败，请稍后再试")

    async def _finalize(self, conn, session, wage: float, source: str, attempt_no: int) -> None:
        case = await _Tx.case_by_id(conn, session["case_id"])
        await self._dao.deactivate_contracts(conn, case["player_id"])
        await self._dao.insert_contract(
            conn,
            case["id"],
            session["id"],
            case["team_id"],
            case["player_id"],
            case["season_number"],
            case["window_seq"],
            wage,
            session["release_fee"],
            source,
        )
        await self._dao.finish_session(conn, session["id"], attempt_no)
        await self._dao.update_case_status(conn, case["id"], "success")

    # ─── window / season ────────────────────────────────────

    async def close_window_cases(self) -> int:
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")

        async def _tx(conn):
            cur_state = await _Tx.league_state(conn)
            if not cur_state:
                raise NegotiationError("赛季状态未初始化")
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM negotiation_cases "
                "WHERE window_seq=? AND status IN ('pending','negotiating')",
                (cur_state["window_seq"],),
            ) as cur:
                row = await cur.fetchone()
            if not row or row["n"] == 0:
                return 0
            await conn.execute(
                "UPDATE negotiation_cases SET status='cancelled', "
                "updated_at=datetime('now','localtime') "
                "WHERE window_seq=? AND status IN ('pending','negotiating')",
                (cur_state["window_seq"],),
            )
            await conn.execute(
                "UPDATE negotiation_sessions SET status='cancelled', "
                "finished_at=datetime('now','localtime') "
                "WHERE status='negotiating' AND case_id IN "
                "(SELECT id FROM negotiation_cases WHERE window_seq=? "
                " AND status='cancelled')",
                (cur_state["window_seq"],),
            )
            return row["n"]

        return await self._db.execute_transaction(_tx)

    async def advance_window(self, updated_by: str) -> dict:
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")

        async def _tx(conn):
            cur_state = await _Tx.league_state(conn)
            if not cur_state:
                raise NegotiationError("赛季状态未初始化")
            active = await _Tx.count_active_cases(conn, cur_state["window_seq"])
            if active > 0:
                raise NegotiationError(
                    f"当前窗口仍有 {active} 个未完成案例，请先取消或关闭窗口案例"
                )
            await self._dao.advance_window(conn, updated_by)
            state2 = await _Tx.league_state(conn)
            await self._dao.insert_window(conn, state2["window_seq"], state2["season_number"])
            return state2["window_seq"]

        new_seq = await self._db.execute_transaction(_tx)
        return {"window_seq": new_seq}

    async def advance_season(self, updated_by: str, reset: bool = False) -> dict:
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")

        current_growth = int(self._cfg.get("growth_age", 25))
        if reset:
            new_growth = 25
        else:
            new_growth = max(1, current_growth - 1)

        async def _tx(conn):
            cur_state = await _Tx.league_state(conn)
            if not cur_state:
                raise NegotiationError("赛季状态未初始化")
            active = await _Tx.count_active_cases(conn, cur_state["window_seq"])
            if active > 0:
                raise NegotiationError(
                    f"当前窗口仍有 {active} 个未完成案例，请先取消或关闭窗口案例"
                )
            await self._dao.advance_season(conn, updated_by)
            state2 = await _Tx.league_state(conn)
            await self._dao.insert_window(conn, state2["window_seq"], state2["season_number"])
            await self._dao.insert_season(conn, state2["season_number"])
            return state2["season_number"], state2["window_seq"]

        season_number, window_seq = await self._db.execute_transaction(_tx)

        if self._persist_cfg is not None:
            try:
                await self._persist_cfg("growth_age", new_growth)
            except Exception as e:
                logger.warning(f"Failed to persist growth_age: {e}")
        self._cfg["growth_age"] = new_growth
        return {
            "season_number": season_number,
            "window_seq": window_seq,
            "growth_age": new_growth,
        }

    # ─── queries ────────────────────────────────────────────

    async def name_window(self, name: str) -> dict:
        from ..utils.security import sanitize_text

        name = sanitize_text(name)
        if not name:
            raise NegotiationError("窗口名称不能为空")
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")

        async def _tx(conn):
            cur_state = await _Tx.league_state(conn)
            if not cur_state:
                raise NegotiationError("赛季状态未初始化")
            await self._dao.rename_window(conn, cur_state["window_seq"], name)

        await self._db.execute_transaction(_tx)
        return {"window_seq": state["window_seq"], "name": name}

    async def name_season(self, name: str) -> dict:
        from ..utils.security import sanitize_text

        name = sanitize_text(name)
        if not name:
            raise NegotiationError("赛季名称不能为空")
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")

        async def _tx(conn):
            cur_state = await _Tx.league_state(conn)
            if not cur_state:
                raise NegotiationError("赛季状态未初始化")
            await self._dao.rename_season(conn, cur_state["season_number"], name)

        await self._db.execute_transaction(_tx)
        return {"season_number": state["season_number"], "name": name}

    async def list_pending_for_player(self, qq: str, page_raw: str | None) -> dict:
        binding = await self._dao.get_binding_by_qq(qq)
        if not binding:
            raise NegotiationError("你尚未绑定球队")
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")
        page, offset = self._page_params(page_raw)
        rows = await self._dao.list_cases(
            state["window_seq"], offset, _PAGE_SIZE, team_id=binding["team_id"], status="pending"
        )
        total = await self._dao.count_cases_in_window(
            state["window_seq"], binding["team_id"], status="pending"
        )
        return {
            "team": binding["team_name"],
            "window_seq": state["window_seq"],
            "window_name": await self._dao.get_window_name(state["window_seq"]),
            "season_number": state["season_number"],
            "season_name": await self._dao.get_season_name(state["season_number"]),
            "page": page,
            "rows": rows,
            "total": total,
        }

    async def list_cases_admin(
        self, window_raw: str | None, team_name: str | None, page_raw: str | None
    ) -> dict:
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")
        if window_raw and str(window_raw).strip().isdigit():
            window_seq = int(str(window_raw).strip())
        else:
            window_seq = state["window_seq"]
        team_id = None
        if team_name:
            team = await self._dao.get_team_by_name(team_name)
            if not team:
                raise NegotiationError(f"球队「{team_name}」不存在")
            team_id = team["id"]
        page, offset = self._page_params(page_raw)
        rows = await self._dao.list_cases(window_seq, offset, _PAGE_SIZE, team_id)
        total = await self._dao.count_cases_in_window(window_seq, team_id)
        window_row = await self._dao.get_window(window_seq)
        window_name = window_row["name"] if window_row else ""
        season_number = window_row["season_number"] if window_row else None
        season_name = (
            await self._dao.get_season_name(season_number) if season_number is not None else ""
        )
        return {
            "window_seq": window_seq,
            "window_name": window_name,
            "season_number": season_number,
            "season_name": season_name,
            "page": page,
            "rows": rows,
            "total": total,
            "current_window": state["window_seq"],
        }

    async def my_contracts(self, qq: str, page_raw: str | None) -> dict:
        binding = await self._dao.get_binding_by_qq(qq)
        if not binding:
            raise NegotiationError("你尚未绑定球队")
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")
        page, offset = self._page_params(page_raw)
        rows = await self._dao.list_contracts(
            state["window_seq"], binding["team_id"], offset, _PAGE_SIZE
        )
        total = await self._dao.count_contracts(state["window_seq"], binding["team_id"])
        return {
            "team": binding["team_name"],
            "window_seq": state["window_seq"],
            "window_name": await self._dao.get_window_name(state["window_seq"]),
            "season_number": state["season_number"],
            "season_name": await self._dao.get_season_name(state["season_number"]),
            "page": page,
            "rows": rows,
            "total": total,
        }

    async def league_status(self) -> dict:
        state = await self._dao.get_league_state()
        if not state:
            raise NegotiationError("赛季状态未初始化")
        return {
            "season_number": state["season_number"],
            "season_name": await self._dao.get_season_name(state["season_number"]),
            "window_seq": state["window_seq"],
            "window_name": await self._dao.get_window_name(state["window_seq"]),
            "growth_age": int(self._cfg.get("growth_age", 25)),
        }


class _Tx:
    """事务内查询适配（conn 直连，避免在事务回调内调用 db 层加锁方法）。"""

    @staticmethod
    async def league_state(conn):
        async with conn.execute("SELECT * FROM league_state WHERE id=1") as cur:
            return await cur.fetchone()

    @staticmethod
    async def count_active_cases(conn, window_seq: int) -> int:
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM negotiation_cases "
            "WHERE window_seq=? AND status IN ('pending','negotiating')",
            (window_seq,),
        ) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    @staticmethod
    async def code_by_hash(conn, code_hash: str):
        async with conn.execute(
            "SELECT * FROM auth_codes WHERE code_hash=?", (code_hash,)
        ) as cur:
            return await cur.fetchone()

    @staticmethod
    async def session_by_case(conn, case_id: int):
        async with conn.execute(
            "SELECT * FROM negotiation_sessions WHERE case_id=?", (case_id,)
        ) as cur:
            return await cur.fetchone()

    @staticmethod
    async def case_by_id(conn, case_id: int):
        async with conn.execute(
            "SELECT * FROM negotiation_cases WHERE id=?", (case_id,)
        ) as cur:
            return await cur.fetchone()

    @staticmethod
    async def count_attempts(conn, session_id: int) -> int:
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM negotiation_attempts WHERE session_id=?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    @staticmethod
    async def last_attempt(conn, session_id: int):
        async with conn.execute(
            "SELECT * FROM negotiation_attempts WHERE session_id=? "
            "ORDER BY attempt_no DESC LIMIT 1",
            (session_id,),
        ) as cur:
            return await cur.fetchone()

    @staticmethod
    async def contract_by_session(conn, session_id: int):
        async with conn.execute(
            "SELECT 1 FROM contracts WHERE session_id=? LIMIT 1", (session_id,)
        ) as cur:
            return await cur.fetchone()
