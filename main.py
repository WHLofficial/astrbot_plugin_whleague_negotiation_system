from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, register

from .config.defaults import (
    _LIST_KEYS,
    DEFAULT_CONFIG,
    PLUGIN_VERSION,
    parse_group_list,
)
from .db.connection import DatabaseManager
from .db.dao import NegotiationDAO
from .db.schema import init_schema
from .utils.rate_limiter import RateLimiter


@register(
    "whleague_negotiation_system",
    "WHLofficial",
    "谈判系统插件：球员合同谈判、球队认证、转会窗口与赛季",
    PLUGIN_VERSION,
)
class NegotiationSystemPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config
        """AstrBot 托管的插件配置（WebUI 中可见、可修改），见 _conf_schema.json。"""

    async def initialize(self) -> None:
        self.db = DatabaseManager()
        await self.db.init()

        self.dao = NegotiationDAO(self.db)

        await init_schema(self.db)

        self.config_cache = await self._load_config_cache()
        self.rate_limiter = RateLimiter()

        from .services.backup_service import BackupService
        from .services.negotiation_service import NegotiationService
        from .services.roster_import_service import RosterImportService

        self.backup_service = BackupService(self.db, self.config_cache)
        self.negotiation_service = NegotiationService(
            self.db,
            self.dao,
            self.config_cache,
            self.rate_limiter,
            persist_cfg=self._persist_config,
        )
        self.import_service = RosterImportService(self.db, self.dao, self.config_cache)

        from .handlers.admin import AdminHandler
        from .handlers.player import PlayerHandler

        self.admin_handler = AdminHandler(self)
        self.player_handler = PlayerHandler(self)

        await self._start_cron_jobs()

        logger.info("Negotiation system plugin initialized.")

    async def _load_config_cache(self) -> dict:
        if self.config is None:
            cache = dict(DEFAULT_CONFIG)
            for key in _LIST_KEYS:
                cache[key] = parse_group_list(cache[key])
            return cache
        cache = {}
        for key, default in DEFAULT_CONFIG.items():
            val = self.config.get(key, default)
            if key in _LIST_KEYS:
                cache[key] = parse_group_list(val)
            else:
                cache[key] = val
        return cache

    async def _persist_config(self, key: str, value) -> None:
        """持久化配置变更（优先 AstrBot 托管配置，其次数据库表）。"""
        if self.config is not None:
            self.config[key] = value
            self.config.save_config()
        else:
            await self.dao.set_config(key, str(value))

    # ─── gates ──────────────────────────────────────────────

    def _is_group_allowed(self, event: AstrMessageEvent) -> bool:
        group_id = event.get_group_id()
        if not group_id:
            return False
        whitelist = self.config_cache.get("group_whitelist", [])
        if not whitelist:
            return True
        return str(group_id) in [str(g) for g in whitelist]

    def is_group_allowed(self, event: AstrMessageEvent) -> bool:
        return self._is_group_allowed(event)

    def is_code_group_allowed(self, event: AstrMessageEvent) -> bool:
        group_id = event.get_group_id()
        if not group_id:
            return False
        code_whitelist = self.config_cache.get("auth_code_group_whitelist", [])
        if code_whitelist:
            return str(group_id) in [str(g) for g in code_whitelist]
        return self._is_group_allowed(event)

    # ─── cron ───────────────────────────────────────────────

    async def _start_cron_jobs(self) -> None:
        try:
            cfg = self.config_cache
            if cfg.get("backup_enabled", True):
                backup_time = cfg.get("backup_time", "04:00")
                hour, minute = self._parse_hhmm(backup_time, 4, 0)
                self._backup_job = await self.context.cron_manager.add_basic_job(
                    name="negotiation_backup",
                    cron_expression=f"{minute} {hour} * * *",
                    handler=self._cron_backup,
                    description="Daily negotiation system backup",
                )
                logger.info(f"Backup cron job scheduled at {backup_time}.")
        except Exception as e:
            logger.warning(f"Failed to schedule cron jobs: {e}")
            self._backup_job = None

    async def _cron_backup(self) -> None:
        try:
            await self.backup_service.run_backup()
        except Exception as e:
            logger.error(f"Scheduled backup failed: {e}")

    @staticmethod
    def _parse_hhmm(value: str, default_hour: int, default_minute: int) -> tuple[int, int]:
        try:
            hour, minute = value.strip().split(":", 1)
            hour, minute = int(hour), int(minute)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except (AttributeError, ValueError, TypeError):
            pass
        return default_hour, default_minute

    async def _remove_cron_jobs(self) -> None:
        """移除已注册的定时任务（terminate 时调用，只删不建）。

        add_basic_job 返回的 CronJob 对象无 remove() 方法，必须经
        cron_manager.delete_job(job_id) 才能真正移除，否则禁用/热更新
        会反复累积重复任务。
        """
        cron_mgr = getattr(self.context, "cron_manager", None)
        job = getattr(self, "_backup_job", None)
        if job:
            job_id = getattr(job, "job_id", None)
            if job_id and cron_mgr and hasattr(cron_mgr, "delete_job"):
                try:
                    await cron_mgr.delete_job(job_id)
                except Exception:
                    pass
        self._backup_job = None

    async def reschedule_cron_jobs(self) -> None:
        """热更新定时任务：移除旧任务后按最新配置重建。"""
        await self._remove_cron_jobs()
        await self._start_cron_jobs()

    # ═══════════════════════════════════════════════════════
    # Admin commands
    # ═══════════════════════════════════════════════════════

    async def _admin_cmd(self, event, handler, *args):
        if not self._is_group_allowed(event):
            return
        async for r in handler(event, *args):
            yield r

    @filter.command("创建球队")
    async def cmd_create_team(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.create_team):
            yield r

    @filter.command("生成认证码")
    async def cmd_generate_code(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.generate_code):
            yield r

    @filter.command("认证码列表")
    async def cmd_list_codes(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.list_codes):
            yield r

    @filter.command("解绑")
    async def cmd_unbind(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.unbind):
            yield r

    @filter.command("创建谈判")
    async def cmd_create_case(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.create_case):
            yield r

    @filter.command("谈判列表")
    async def cmd_list_cases(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.list_cases):
            yield r

    @filter.command("取消谈判")
    async def cmd_cancel_case(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.cancel_case):
            yield r

    @filter.command("关闭窗口案例")
    async def cmd_close_window(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.close_window_cases):
            yield r

    @filter.command("进入下个窗口")
    async def cmd_advance_window(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.advance_window):
            yield r

    @filter.command("进入下个赛季")
    async def cmd_advance_season(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.advance_season):
            yield r

    @filter.command("命名")
    async def cmd_name(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.name_entity):
            yield r

    @filter.command("导入球员文件")
    async def cmd_import_file(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.import_file):
            yield r

    @filter.command("确认导入球员")
    async def cmd_confirm_import(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.confirm_import):
            yield r

    @filter.command("导入列表")
    async def cmd_import_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.import_list):
            yield r

    @filter.command("谈判设置")
    async def cmd_set_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.set_config):
            yield r

    @filter.command("谈判查看配置")
    async def cmd_view_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.view_config):
            yield r

    @filter.command("谈判添加管理")
    async def cmd_add_admin(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.add_admin):
            yield r

    @filter.command("谈判删除管理")
    async def cmd_remove_admin(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        async for r in self._admin_cmd(event, self.admin_handler.remove_admin):
            yield r

    # ═══════════════════════════════════════════════════════
    # Player commands
    # ═══════════════════════════════════════════════════════

    @filter.command("绑定球队")
    async def cmd_bind_team(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.bind_team(event):
            yield r

    @filter.command("我的球队")
    async def cmd_my_team(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.my_team(event):
            yield r

    @filter.command("待谈判")
    async def cmd_pending(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.pending_cases(event):
            yield r

    @filter.command("开始谈判")
    async def cmd_start_negotiation(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.start_negotiation(event):
            yield r

    @filter.command("报价")
    async def cmd_offer(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.offer(event):
            yield r

    @filter.command("我的合同")
    async def cmd_my_contracts(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.my_contracts(event):
            yield r

    @filter.command("赛季状态")
    async def cmd_league_status(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not self._is_group_allowed(event):
            return
        async for r in self.player_handler.league_status(event):
            yield r

    # ═══════════════════════════════════════════════════════
    # File capture (group message)
    # ═══════════════════════════════════════════════════════

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        if not self._is_group_allowed(event):
            return
        messages = event.get_messages()
        file_comps = [m for m in messages if isinstance(m, File)]
        if not file_comps:
            return
        qq = event.get_sender_id()
        if not (event.is_admin() or await self.dao.is_admin(qq)):
            return
        for comp in file_comps:
            try:
                file_path = await comp.get_file()
                if not file_path:
                    continue
                target = self.import_service.save_uploaded(file_path, comp.name or "")
                data, errors, skipped = self.import_service.parse_rows(target)
                lines = [
                    f"📄 收到文件 {target.name}: 可导入 {len(data)} 行（跳过 {skipped} 行）"
                ]
                for uid, foreign, _ in data[:3]:
                    lines.append(f"· {uid} {foreign}")
                if errors:
                    lines.append(f"⚠️ {len(errors)} 行错误: {errors[0]}")
                lines.append("回复 /确认导入球员 <文件名> 执行导入")
                await event.send(MessageChain().message("\n".join(lines)))
            except (ValueError, FileNotFoundError) as e:
                await event.send(MessageChain().message(str(e)))
            except Exception as e:
                logger.error(f"File import capture error: {e}")
                await event.send(MessageChain().message("文件接收失败，已记录错误"))

    # ═══════════════════════════════════════════════════════
    # Teardown
    # ═══════════════════════════════════════════════════════

    async def terminate(self) -> None:
        await self._remove_cron_jobs()
        if hasattr(self, "db"):
            await self.db.close()
        logger.info("Negotiation system plugin terminated.")
