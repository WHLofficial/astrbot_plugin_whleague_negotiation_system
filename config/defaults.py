import json
import os

PLUGIN_VERSION = "1.1.0"
"""插件版本号，与 metadata.yaml 保持一致。"""

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
)

_TYPE_DEFAULTS = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "list": [],
}

_TYPE_MAP = {
    "int": int,
    "float": float,
    "bool": bool,
    "string": str,
    "list": str,
}


def _load_schema() -> dict:
    if not os.path.exists(_SCHEMA_PATH):
        raise RuntimeError(f"缺少插件配置 schema 文件: {_SCHEMA_PATH}")
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    for key, meta in schema.items():
        if meta.get("type") not in _TYPE_DEFAULTS:
            raise RuntimeError(f"配置项 {key} 的类型 {meta.get('type')} 不受支持")
    return schema


_SCHEMA = _load_schema()

DEFAULT_CONFIG = {
    key: meta.get("default", _TYPE_DEFAULTS[meta["type"]])
    for key, meta in _SCHEMA.items()
}

TYPE_MAP = {key: _TYPE_MAP[meta["type"]] for key, meta in _SCHEMA.items()}

_LIST_KEYS = tuple(key for key, meta in _SCHEMA.items() if meta["type"] == "list")

# 小数类配置的允许区间（闭区间）
_FLOAT_RANGES = {
    "wage_param_a": (1e-9, 1e6),
    "wage_param_b": (1e-9, 100.0),
    "wage_param_c": (1e-9, 10.0),
    "attempt_decay_multiplier": (0.0, 1.0),
    "wage_min": (0.0, 1e6),
    "wage_max": (0.0, 1e7),
}

# 整数配置业务上限（超出拒绝）；未列出的整数键不设上限
_INT_UPPER_BOUNDS = {
    "growth_age": 60,
    "negotiation_max_attempts": 10,
    "auth_code_default_expire_hours": 24 * 365,
    "auth_code_attempt_limit": 100,
    "auth_code_lock_minutes": 24 * 60,
    "import_col_uid": 30,
    "import_col_foreign_name": 30,
    "import_col_chinese_name": 30,
    "import_max_rows": 1_000_000,
    "import_batch_size": 100_000,
    "import_max_file_size_mb": 1024,
    "import_max_files": 10_000,
    "ca_max": 300,
    "age_min": 100,
    "age_max": 100,
    "fee_max": 10**12,
    "backup_keep_count": 10_000,
}

# 整数配置业务下限
_INT_LOWER_BOUNDS = {
    "negotiation_max_attempts": 1,
    "auth_code_default_expire_hours": 1,
    "auth_code_attempt_limit": 1,
    "auth_code_lock_minutes": 1,
    "import_col_uid": 0,
    "import_col_foreign_name": 0,
    "import_col_chinese_name": 0,
    "import_max_rows": 1,
    "import_batch_size": 1,
    "import_max_file_size_mb": 1,
    "import_max_files": 1,
    "ca_max": 1,
    "age_min": 1,
    "age_max": 1,
    "fee_max": 1,
    "backup_keep_count": 1,
    "growth_age": 1,
}

# 时间类配置（HH:MM）
_TIME_KEYS = ("backup_time",)


def parse_group_list(raw):
    """将配置中的群白名单解析为列表，兼容 JSON 数组或逗号分隔文本。"""
    if isinstance(raw, (list, tuple)):
        return [str(g) for g in raw if str(g).strip()]
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(g) for g in data if str(g).strip()]
    except json.JSONDecodeError:
        pass
    return [g.strip() for g in s.split(",") if g.strip()]


def validate_and_cast(key: str, raw: str):
    """校验并转换管理员通过 /谈判设置 传入的配置值。"""
    if key not in DEFAULT_CONFIG:
        raise ValueError(f"未知配置项: {key}")

    if key in _LIST_KEYS:
        return parse_group_list(raw)

    if key in _TIME_KEYS:
        import re

        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", raw.strip())
        if not m:
            raise ValueError(f"{key} 需为 HH:MM 格式")
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"

    t = TYPE_MAP.get(key, str)
    if t is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError(f"配置 {key} 需为布尔值 (true/false/1/0)")
    if t is int:
        try:
            parsed = int(raw.strip())
        except ValueError:
            raise ValueError(f"配置 {key} 需为整数")
        lower = _INT_LOWER_BOUNDS.get(key)
        if lower is not None and parsed < lower:
            raise ValueError(f"配置 {key} 不能小于 {lower}")
        upper = _INT_UPPER_BOUNDS.get(key)
        if upper is not None and parsed > upper:
            raise ValueError(f"配置 {key} 不能大于 {upper}")
        return parsed
    if t is float:
        try:
            parsed = float(raw.strip())
        except ValueError:
            raise ValueError(f"配置 {key} 需为数字")
        lo, hi = _FLOAT_RANGES.get(key, (None, None))
        if lo is not None and parsed < lo:
            raise ValueError(f"配置 {key} 不能小于 {lo}")
        if hi is not None and parsed > hi:
            raise ValueError(f"配置 {key} 不能大于 {hi}")
        return parsed
    return raw.strip()
