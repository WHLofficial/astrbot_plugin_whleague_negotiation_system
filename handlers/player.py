from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..services.negotiation_service import NegotiationError
from ..utils.security import format_wage, parse_float_2dp, parse_int

_STATUS_ICONS = {"pending": "🕐", "negotiating": "💬", "success": "✅", "cancelled": "❌"}


class PlayerHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    async def _run(self, event, coro):
        try:
            return await coro
        except NegotiationError as e:
            return {"error": str(e)}
        except (ValueError, IndexError) as e:
            return {"error": f"参数错误: {e}"}
        except Exception as e:
            logger.error(f"Player handler error: {e}")
            return {"error": "操作失败，已记录错误"}

    async def bind_team(self, event) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /绑定球队 <认证码>")
            return
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        result = await self._run(
            event,
            self._plugin.negotiation_service.bind_team(qq, parts[1], group_id),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(f"✅ 认证成功！你已绑定球队「{result['team_name']}」")

    async def my_team(self, event) -> AsyncGenerator[MessageEventResult, None]:
        try:
            binding = await self._plugin.dao.get_binding_by_qq(event.get_sender_id())
            if not binding:
                yield event.plain_result("你尚未绑定球队，请先 /绑定球队")
                return
            state = await self._plugin.dao.get_league_state()
            if not state:
                yield event.plain_result("赛季状态未初始化")
                return
            members = await self._plugin.dao.get_bindings_by_team(binding["team_id"])
            season_label = self._season_label(
                state["season_number"],
                await self._plugin.dao.get_season_name(state["season_number"]),
            )
            window_label = self._window_label(
                state["window_seq"],
                await self._plugin.dao.get_window_name(state["window_seq"]),
            )
            lines = [
                f"🏟 我的球队: {binding['team_name']}",
                f"· 绑定成员: {len(members)} 人",
                f"· 当前: {season_label} / {window_label}",
            ]
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"My team error: {e}")
            yield event.plain_result("查询失败，已记录错误")

    @staticmethod
    def _window_label(window_seq: int, name: str) -> str:
        return f"窗口 {window_seq}（{name}）" if name else f"窗口 {window_seq}"

    @staticmethod
    def _season_label(season_number: int, name: str) -> str:
        return f"第 {season_number} 赛季（{name}）" if name else f"第 {season_number} 赛季"

    def _wage_unit(self) -> str:
        return str(self._plugin.config_cache.get("wage_unit", "M/半赛季"))

    def _fee_unit(self) -> str:
        return str(self._plugin.config_cache.get("fee_unit", "M"))

    async def pending_cases(self, event) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        page_raw = parts[1] if len(parts) >= 2 else None
        result = await self._run(
            event,
            self._plugin.negotiation_service.list_pending_for_player(
                event.get_sender_id(), page_raw
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        rows = result["rows"]
        window_label = self._window_label(
            result["window_seq"], result.get("window_name", "")
        )
        if not rows:
            yield event.plain_result(
                f"🕐 球队「{result['team']}」当前{window_label}暂无待谈判球员"
            )
            return
        lines = [
            f"🕐 待谈判球员 (第{result['page']}页): "
            f"球队「{result['team']}」{window_label}"
        ]
        for c in rows:
            lines.append(
                f"#{c['id']} {c['foreign_name']}({c['player_uid']}) "
                f"| 年龄{c['age']} CA{c['ca']} PA{c['pa']} 旧违约金{c['old_release_fee']}"
            )
        lines.append("回复 /开始谈判 <案例ID> <新违约金> 开始")
        yield event.plain_result("\n".join(lines))

    async def start_negotiation(self, event) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        if len(parts) < 3:
            yield event.plain_result("用法: /开始谈判 <案例ID> <新违约金>")
            return
        try:
            case_id = parse_int(parts[1], min_val=1)
            release_fee = parse_int(parts[2], min_val=1)
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
            return
        result = await self._run(
            event,
            self._plugin.negotiation_service.start_negotiation(
                case_id, event.get_sender_id(), release_fee
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        session = await self._plugin.dao.get_session_by_case(case_id)
        attempts = session["attempt_count"] if session else 0
        used = f"（已报价 {attempts} 次）" if attempts else ""
        yield event.plain_result(
            f"💬 谈判已开始 (案例 #{case_id})\n"
            f"· 新违约金: {result['release_fee']} {self._fee_unit()}\n"
            f"· 预期工资: {format_wage(result['expected_wage'])} {self._wage_unit()}\n"
            f"· 回复 /报价 {case_id} <工资> 出价（可重设违约金重报）{used}"
        )

    async def offer(self, event) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        if len(parts) < 3:
            yield event.plain_result("用法: /报价 <案例ID> <工资>")
            return
        try:
            case_id = parse_int(parts[1], min_val=1)
        except ValueError:
            yield event.plain_result("案例 ID 无效")
            return
        cfg = self._plugin.config_cache
        try:
            wage = parse_float_2dp(
                parts[2], min_val=cfg.get("wage_min", 0.01), max_val=cfg.get("wage_max", 20.0)
            )
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
            return
        qq = event.get_sender_id()
        group_id = event.get_group_id()
        if not self._plugin.rate_limiter.check_user("offer", qq, group_id, 1):
            yield event.plain_result("操作过于频繁，请稍后再试")
            return
        result = await self._run(
            event,
            self._plugin.negotiation_service.offer_wage(case_id, qq, wage),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        if result["result"] == "success":
            yield event.plain_result(
                f"🎉 谈判成功！以 {format_wage(result['wage'])} {self._wage_unit()} 达成协议（第 {result['attempt_no']} 次报价）"
            )
        elif result["result"] == "forced":
            yield event.plain_result(
                f"📄 三次报价均未达成，已按预期工资 {format_wage(result['wage'])} {self._wage_unit()} 自动签约（第 {result['attempt_no']} 次报价后）"
            )
        else:
            yield event.plain_result(
                f"❌ 第 {result['attempt_no']} 次报价未达成，剩余 {result['remaining']} 次机会"
                f"（当前报价把握：{result['tier']}）"
            )

    async def my_contracts(self, event) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        page_raw = parts[1] if len(parts) >= 2 else None
        result = await self._run(
            event,
            self._plugin.negotiation_service.my_contracts(event.get_sender_id(), page_raw),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        rows = result["rows"]
        window_label = self._window_label(
            result["window_seq"], result.get("window_name", "")
        )
        if not rows:
            yield event.plain_result(
                f"📄 球队「{result['team']}」当前{window_label}暂无有效合同"
            )
            return
        lines = [
            f"📄 有效合同 (第{result['page']}页): 球队「{result['team']}」{window_label}"
        ]
        for ct in rows:
            mark = "自动" if ct["source"] == "forced" else "谈判"
            lines.append(
                f"· {ct['foreign_name']}({ct['player_uid']}) "
                f"| 工资 {format_wage(ct['wage'])} {self._wage_unit()} | 违约金 {ct['release_fee']} {self._fee_unit()} | {mark}"
            )
        yield event.plain_result("\n".join(lines))

    async def league_status(self, event) -> AsyncGenerator[MessageEventResult, None]:
        result = await self._run(
            event, self._plugin.negotiation_service.league_status()
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        season_label = self._season_label(
            result["season_number"], result.get("season_name", "")
        )
        window_label = self._window_label(
            result["window_seq"], result.get("window_name", "")
        )
        yield event.plain_result(
            f"🏆 当前赛季: {season_label}\n"
            f"🪟 转会窗口: {window_label}\n"
            f"📈 成长年龄: {result['growth_age']}"
        )
