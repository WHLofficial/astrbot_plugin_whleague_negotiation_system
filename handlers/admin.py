import asyncio
import random
import time
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..services.negotiation_service import NegotiationError
from ..utils.security import (
    parse_int,
    parse_qq,
    parse_qq_arg,
)

_CLEAR_TOKEN_TTL = 300.0


def _window_label(window_seq: int, name: str) -> str:
    return f"窗口 {window_seq}（{name}）" if name else f"窗口 {window_seq}"


def _season_label(season_number, name: str) -> str:
    if season_number is None:
        return "未知赛季"
    return f"第 {season_number} 赛季（{name}）" if name else f"第 {season_number} 赛季"


class AdminHandler:
    def __init__(self, plugin):
        self._plugin = plugin
        self._pending_closes: dict[str, dict] = {}
        """qq -> {"token", "expires_at"}"""

    async def _require_admin(self, event) -> bool:
        if event.is_admin():
            return True
        return await self._plugin.dao.is_admin(event.get_sender_id())

    async def _deny(self, event) -> AsyncGenerator[MessageEventResult, None]:
        yield event.plain_result("你没有权限执行此操作")

    async def _run(self, event, coro):
        """执行业务协程并捕获业务错误。"""
        try:
            return await coro
        except NegotiationError as e:
            return {"error": str(e)}
        except (ValueError, IndexError) as e:
            return {"error": f"参数错误: {e}"}
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Admin handler error: {e}")
            return {"error": "操作失败，已记录错误"}

    # ─── teams ──────────────────────────────────────────────

    async def create_team(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法: /创建球队 <队名>")
            return
        result = await self._run(
            event,
            self._plugin.negotiation_service.create_team(
                parts[1], event.get_sender_id()
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(f"✅ 已创建球队「{result['name']}」")

    # ─── auth codes ─────────────────────────────────────────

    async def generate_code(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        if not self._plugin.is_code_group_allowed(event):
            yield event.plain_result("当前群不在认证码生成白名单内")
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /生成认证码 <队名> [时长小时]")
            return
        hours = None
        if len(parts) >= 3:
            try:
                hours = parse_int(parts[2], min_val=1, max_val=24 * 365)
            except ValueError:
                yield event.plain_result("时长需为大于 0 的整数小时")
                return
        result = await self._run(
            event,
            self._plugin.negotiation_service.generate_code(
                parts[1], event.get_sender_id(), hours
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(
            f"🔑 已为球队「{result['team_name']}」生成认证码:\n"
            f"`{result['code']}`\n"
            f"有效期至 {result['expires_at']}（1 次有效）"
        )

    async def list_codes(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=1)
        team_name = parts[1] if len(parts) >= 2 else None
        result = await self._run(
            event, self._plugin.negotiation_service.list_codes(team_name)
        )
        if isinstance(result, dict) and "error" in result:
            yield event.plain_result(result["error"])
            return
        if not result:
            yield event.plain_result("暂无认证码记录")
            return
        lines = ["🔑 认证码列表"]
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for c in result:
            if c["used_count"] > 0:
                state = "❌ 已用"
            elif c["expires_at"] < now:
                state = "⏳ 已过期"
            else:
                state = "✅ 有效"
            lines.append(
                f"· #{c['id']} {c['team_name']} | {state} | 截止 {c['expires_at']}"
            )
        yield event.plain_result("\n".join(lines))

    # ─── unbind ─────────────────────────────────────────────

    async def unbind(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) != 2:
            yield event.plain_result("用法: /解绑 <QQ> 或 /解绑 <队名>（队内多人时需指定 QQ）")
            return
        target = parts[1]
        qq = parse_qq_arg(target)
        if qq is None:
            # 先按队名精确匹配（防「纯数字/含括号队名」被误判为 QQ），再兜底 QQ 解析
            team = await self._plugin.dao.get_team_by_name(target)
            if team is not None:
                result = await self._run(
                    event, self._plugin.negotiation_service.unbind_team(target)
                )
                if "error" in result:
                    yield event.plain_result(result["error"])
                    return
                yield event.plain_result(f"✅ 已解绑 {result['qq']} 与球队「{result['team_name']}」")
                return
            if target.lstrip("@").isdigit():
                qq = target.lstrip("@")
        if qq is not None:
            result = await self._run(
                event, self._plugin.negotiation_service.unbind_qq(qq)
            )
        else:
            result = await self._run(
                event, self._plugin.negotiation_service.unbind_team(target)
            )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(f"✅ 已解绑 {result['qq']} 与球队「{result['team_name']}」")

    # ─── cases ──────────────────────────────────────────────

    async def create_case(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=6)
        if len(parts) < 7:
            yield event.plain_result(
                "用法: /创建谈判 <队名> <球员UID> <年龄> <CA> <PA> <旧违约金>"
            )
            return
        try:
            age = parse_int(parts[3])
            ca = parse_int(parts[4])
            pa = parse_int(parts[5])
            old_fee = parse_int(parts[6])
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
            return
        result = await self._run(
            event,
            self._plugin.negotiation_service.create_case(
                parts[2], parts[1], age, ca, pa, old_fee, event.get_sender_id()
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        ref = ""
        if result.get("ref_fee") is not None:
            fee_unit = str(self._plugin.config_cache.get("fee_unit", "M"))
            ref = f"\n参考: 该球员上次合同违约金 {result['ref_fee']} {fee_unit}"
        yield event.plain_result(
            f"✅ 已创建谈判案例 #{result['case_id']}: "
            f"{result['player']} → {result['team']}（窗口 {result['window_seq']}）{ref}"
        )

    async def list_cases(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        window_raw = None
        page_raw = None
        if len(parts) >= 2 and parts[1].isdigit():
            window_raw = parts[1]
            if len(parts) >= 3:
                page_raw = parts[2]
        elif len(parts) >= 2:
            page_raw = parts[1]
        result = await self._run(
            event,
            self._plugin.negotiation_service.list_cases_admin(
                window_raw, None, page_raw
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        rows = result["rows"]
        season_label = _season_label(
            result.get("season_number"), result.get("season_name", "")
        )
        window_label = _window_label(
            result["window_seq"], result.get("window_name", "")
        )
        if not rows:
            yield event.plain_result(
                f"{season_label} · {window_label} 暂无谈判案例"
            )
            return
        status_icons = {"pending": "🕐", "negotiating": "💬", "success": "✅", "cancelled": "❌"}
        lines = [
            f"📋 谈判案例 ({season_label} · {window_label}, 第{result['page']}页, 共{result['total']}条)"
        ]
        for c in rows:
            lines.append(
                f"{status_icons.get(c['status'], '·')} #{c['id']} {c['foreign_name']}({c['player_uid']}) "
                f"→ {c['team_name']} | 年龄{c['age']} CA{c['ca']} PA{c['pa']} "
                f"旧违约金{c['old_release_fee']} | {c['status']}"
            )
        yield event.plain_result("\n".join(lines))

    async def cancel_case(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /取消谈判 <案例ID>")
            return
        try:
            case_id = parse_int(parts[1], min_val=1)
        except ValueError:
            yield event.plain_result("案例 ID 无效")
            return
        result = await self._run(
            event, self._plugin.negotiation_service.cancel_case(case_id)
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(f"✅ 已取消谈判案例 #{case_id}")

    # ─── window / season ────────────────────────────────────

    async def close_window_cases(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        qq = event.get_sender_id()
        if len(parts) >= 2:
            pending = self._pending_closes.pop(qq, None)
            if not pending:
                yield event.plain_result("没有待确认的关闭操作")
                return
            if time.time() > pending["expires_at"]:
                yield event.plain_result("验证码已过期，请重新发起")
                return
            if pending["token"] != parts[1].strip():
                yield event.plain_result("验证码错误")
                return
            result = await self._run(
                event, self._plugin.negotiation_service.close_window_cases()
            )
            if isinstance(result, dict) and "error" in result:
                yield event.plain_result(result["error"])
                return
            yield event.plain_result(f"✅ 已关闭窗口案例 {result} 个")
            return

        # 预检：当前窗口是否有活跃案例
        state = await self._plugin.dao.get_league_state()
        if state:
            active = await self._plugin.dao.count_active_cases_in_window(
                state["window_seq"]
            )
            if active == 0:
                yield event.plain_result("当前窗口没有活跃案例，无需关闭")
                return
        token = str(random.randint(100000, 999999))
        self._prune_pending_closes()
        self._pending_closes[qq] = {
            "token": token,
            "expires_at": time.time() + _CLEAR_TOKEN_TTL,
        }
        yield event.plain_result(
            f"⚠️ 将取消当前窗口所有进行中/待谈判案例，不可恢复！\n"
            f"请回复 /关闭窗口案例 {token} 确认（5 分钟内有效）。"
        )

    def _prune_pending_closes(self) -> None:
        now = time.time()
        for qq in [k for k, v in self._pending_closes.items() if v["expires_at"] <= now]:
            del self._pending_closes[qq]

    async def advance_window(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        result = await self._run(
            event, self._plugin.negotiation_service.advance_window(event.get_sender_id())
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        yield event.plain_result(f"✅ 已进入第 {result['window_seq']} 个转会窗口")

    async def advance_season(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        reset = False
        if len(parts) >= 2:
            if parts[1].strip() == "0":
                reset = True
            else:
                yield event.plain_result("参数仅支持 0（恢复成长年龄）或省略（成长年龄-1）")
                return
        result = await self._run(
            event,
            self._plugin.negotiation_service.advance_season(
                event.get_sender_id(), reset=reset
            ),
        )
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        mode = "恢复至 25" if reset else "成长年龄 -1"
        yield event.plain_result(
            f"✅ 已进入第 {result['season_number']} 赛季（窗口 {result['window_seq']}）\n"
            f"成长年龄: {result['growth_age']}（{mode}）"
        )

    async def name_entity(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=2)
        if len(parts) < 3 or parts[1] not in ("窗口", "赛季"):
            yield event.plain_result("用法: /命名 <窗口|赛季> <名称>")
            return
        target = parts[1]
        svc = self._plugin.negotiation_service
        if target == "窗口":
            result = await self._run(event, svc.name_window(parts[2]))
        else:
            result = await self._run(event, svc.name_season(parts[2]))
        if "error" in result:
            yield event.plain_result(result["error"])
            return
        seq = result.get("window_seq", result.get("season_number"))
        yield event.plain_result(f"✅ 已命名{target} {seq} 为「{result['name']}」")

    # ─── import ─────────────────────────────────────────────

    async def import_file(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法: /导入球员文件 <文件名>")
            return
        try:
            file_path = self._plugin.import_service.check_file(parts[1])
        except (FileNotFoundError, ValueError) as e:
            yield event.plain_result(str(e))
            return
        data, errors, skipped = await asyncio.to_thread(self._plugin.import_service.parse_rows, file_path)
        require_confirm = self._plugin.config_cache.get("import_require_confirm", True)
        if require_confirm:
            lines = [f"📄 {file_path.name}: 可导入 {len(data)} 行（跳过 {skipped} 行）"]
            for uid, foreign, _ in data[:3]:
                lines.append(f"· {uid} {foreign}")
            if errors:
                lines.append(f"⚠️ {len(errors)} 行错误: " + errors[0])
            lines.append("回复 /确认导入球员 <文件名> 执行导入")
            yield event.plain_result("\n".join(lines))
            return
        result = await self._do_import(event, file_path, data, errors)
        yield event.plain_result(result)

    async def confirm_import(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法: /确认导入球员 <文件名>")
            return
        try:
            file_path = self._plugin.import_service.check_file(parts[1])
        except (FileNotFoundError, ValueError) as e:
            yield event.plain_result(str(e))
            return
        data, errors, skipped = await asyncio.to_thread(self._plugin.import_service.parse_rows, file_path)
        result = await self._do_import(event, file_path, data, errors)
        yield event.plain_result(result)

    async def _do_import(self, event, file_path, data, errors) -> str:
        try:
            stats = await self._plugin.import_service.import_rows(
                data, event.get_sender_id()
            )
        except Exception as e:
            logger.error(f"Import failed: {e}")
            return "导入失败，已记录错误"
        lines = [
            f"✅ 导入完成: 新增 {stats['added']} 人 / 覆盖 {stats['updated']} 人"
        ]
        if errors:
            shown = errors[:5]
            lines.append(f"⚠️ 跳过 {len(errors)} 行错误:")
            lines.extend("· " + e for e in shown)
            if len(errors) > 5:
                lines.append(f"  … 其余 {len(errors) - 5} 行")
        return "\n".join(lines)

    async def import_list(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        try:
            parts = event.get_message_str().split()
            page_raw = parts[1] if len(parts) >= 2 else None
            page = 1
            if page_raw and page_raw.isdigit():
                page = max(1, int(page_raw))
            files = self._plugin.import_service.list_files()
            total = len(files)
            start = (page - 1) * 10
            shown = files[start : start + 10]
            if not shown:
                yield event.plain_result("导入目录暂无文件")
                return
            lines = [f"📁 导入目录文件 (第{page}页, 共{total}个)"]
            for f in shown:
                lines.append(f"· {f.name} ({f.stat().st_size // 1024} KB)")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"Import list error: {e}")
            yield event.plain_result("查询失败，已记录错误")

    # ─── config / admins ────────────────────────────────────

    async def set_config(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result("用法: /谈判设置 <配置项> <值>")
            return
        key = parts[1]
        try:
            from ..config.defaults import validate_and_cast

            parsed = validate_and_cast(key, parts[2])
        except ValueError as e:
            yield event.plain_result(f"参数错误: {e}")
            return
        new_cache = dict(self._plugin.config_cache)
        import_col_keys = ("import_col_uid", "import_col_foreign_name", "import_col_chinese_name")
        old_import_cols = {k: new_cache.get(k) for k in import_col_keys}
        new_cache[key] = parsed
        if key == "wage_min":
            if parsed > new_cache.get("wage_max", 20.0):
                yield event.plain_result("wage_min 不能大于 wage_max")
                return
        if key == "wage_max":
            if parsed < new_cache.get("wage_min", 0.01):
                yield event.plain_result("wage_max 不能小于 wage_min")
                return
        if key == "age_min":
            if parsed > new_cache.get("age_max", 50):
                yield event.plain_result("age_min 不能大于 age_max")
                return
        if key == "age_max":
            if parsed < new_cache.get("age_min", 14):
                yield event.plain_result("age_max 不能小于 age_min")
                return
        if key in import_col_keys:
            others = [v for k2, v in old_import_cols.items() if k2 != key]
            if parsed and parsed in others:
                yield event.plain_result("导入列位不能与其他列位重复")
                return
        self._plugin.config_cache[key] = parsed
        await self._plugin.persist_config(key, parsed)
        if key == "backup_time" or key == "backup_enabled":
            await self._plugin.reschedule_cron_jobs()
        yield event.plain_result(f"已更新配置 {key} = {parsed}")

    async def view_config(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._require_admin(event):
            async for r in self._deny(event):
                yield r
            return
        from ..config.defaults import DEFAULT_CONFIG

        lines = ["⚙ 当前配置"]
        for key, default in DEFAULT_CONFIG.items():
            val = self._plugin.config_cache.get(key, default)
            if isinstance(val, list):
                display = ",".join(str(v) for v in val) if val else "(空)"
            elif isinstance(val, bool):
                display = str(val).lower()
            else:
                display = str(val)
            lines.append(f"{key} = {display}")
        yield event.plain_result("\n".join(lines))

    async def add_admin(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not event.is_admin():
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /谈判添加管理 <QQ>")
            return
        target = parse_qq_arg(parts[1]) or parts[1]
        try:
            qq = parse_qq(target)
        except ValueError:
            yield event.plain_result("QQ 参数无效")
            return
        await self._plugin.dao.add_admin(qq, event.get_sender_id())
        yield event.plain_result(f"已将 {qq} 添加为谈判系统管理员")

    async def remove_admin(self, event) -> AsyncGenerator[MessageEventResult, None]:
        if not event.is_admin():
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /谈判删除管理 <QQ>")
            return
        target = parse_qq_arg(parts[1]) or parts[1]
        try:
            qq = parse_qq(target)
        except ValueError:
            yield event.plain_result("QQ 参数无效")
            return
        await self._plugin.dao.remove_admin(qq)
        yield event.plain_result(f"已删除 {qq} 的管理员权限")

