"""测试公共环境：临时数据库 + 服务装配。

说明：以插件包全名（astrbot_plugin_whleague_negotiation_system.*）绝对导入，
需将 PLUGINS_DIR 加入 sys.path（与积分系统测试一致）。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(PLUGIN_ROOT)
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

from .stubs import install_stubs

install_stubs()

from astrbot_plugin_whleague_negotiation_system.config.defaults import DEFAULT_CONFIG  # noqa: E402
from astrbot_plugin_whleague_negotiation_system.db.connection import DatabaseManager  # noqa: E402
from astrbot_plugin_whleague_negotiation_system.db.dao import NegotiationDAO  # noqa: E402
from astrbot_plugin_whleague_negotiation_system.db.schema import init_schema  # noqa: E402
from astrbot_plugin_whleague_negotiation_system.services.negotiation_service import (  # noqa: E402
    NegotiationService,
)
from astrbot_plugin_whleague_negotiation_system.services.roster_import_service import (  # noqa: E402
    RosterImportService,
)
from astrbot_plugin_whleague_negotiation_system.utils.rate_limiter import RateLimiter  # noqa: E402


class TestEnv:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(str(Path(self._tmp.name) / "test.db"))
        self.dao = NegotiationDAO(self.db)
        self.cfg = dict(DEFAULT_CONFIG)
        # 既有测试默认禁用直接失败（阈值取极小数，成功率恒不低于该值）
        self.cfg["agent_tiers"] = (
            '{"1":{"threshold":1e-9,"probability":0.3},'
            '"2":{"threshold":2e-9,"probability":0.5},'
            '"3":{"threshold":3e-9,"probability":0.7}}'
        )
        self.limiter = RateLimiter()
        self.service = NegotiationService(self.db, self.dao, self.cfg, self.limiter)
        self.import_service = RosterImportService(self.db, self.dao, self.cfg)

    async def setup(self):
        await self.db.init()
        await init_schema(self.db)
        return self

    async def teardown(self):
        await self.db.close()
        self._tmp.cleanup()
