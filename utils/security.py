import re
import secrets

_MAX_TEXT_LENGTH = 50
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

_AUTH_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
"""认证码字符集（排除易混淆字符 0/O/1/l/I）。"""

_UID_MAX_LENGTH = 30


def sanitize_text(text: str) -> str:
    """剥离控制字符并截断，防止构造多行伪造消息。"""
    if not text:
        return ""
    return _CTRL_RE.sub("", str(text)).strip()[:_MAX_TEXT_LENGTH]


def sanitize_uid(text: str) -> str:
    """球员 UID 清洗：剥离控制字符、截断，保持原始字符（允许数字/字母等）。"""
    if not text:
        return ""
    return _CTRL_RE.sub("", str(text)).strip()[:_UID_MAX_LENGTH]


def sanitize_filename(text: str) -> str:
    """文件名清洗：仅保留安全字符，防止路径穿越。"""
    cleaned = _CTRL_RE.sub("", str(text or "")).strip()
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]', "_", cleaned)
    if cleaned in ("", ".", ".."):
        return "file"
    return cleaned


def parse_int(raw: str, min_val: int | None = None, max_val: int | None = None) -> int:
    try:
        val = int(str(raw).strip())
    except (ValueError, TypeError):
        raise ValueError(f"Invalid integer: {raw}")
    if min_val is not None and val < min_val:
        raise ValueError(f"Value {val} is below minimum {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"Value {val} exceeds maximum {max_val}")
    return val


def parse_float_2dp(raw: str, min_val: float | None = None, max_val: float | None = None) -> float:
    """解析浮点数；拒绝超过 2 位小数的输入（防静默舍入改写用户输入）。"""
    s = str(raw).strip()
    if not re.match(r"^\d+(\.\d{1,2})?$", s):
        raise ValueError(f"最多 2 位小数: {raw}")
    val = float(s)
    if min_val is not None and val < min_val:
        raise ValueError(f"Value {val} is below minimum {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"Value {val} exceeds maximum {max_val}")
    return val


def parse_qq(raw: str) -> str:
    cleaned = str(raw).strip().lstrip("@")
    if not cleaned.isdigit():
        raise ValueError(f"Invalid QQ number: {raw}")
    return cleaned


def parse_qq_arg(raw: str) -> str | None:
    """从 @用户 / @昵称(QQ) / 昵称(QQ) / [CQ:at,qq=...] 形式中提取 QQ 号。"""
    s = str(raw).strip()
    if not s:
        return None
    m = re.search(r"\[CQ:at,qq=(\d+)\]", s)
    if m:
        return m.group(1)
    m = re.search(r"\((\d+)\)", s)
    if m:
        return m.group(1)
    if s.startswith("@"):
        m = re.match(r"^@(\d+)$", s)
        if m:
            return m.group(1)
    return None


def generate_auth_code(length: int = 8) -> str:
    """生成认证码（secrets 随机，排除易混淆字符）。"""
    return "".join(secrets.choice(_AUTH_CODE_ALPHABET) for _ in range(length))


def hash_auth_code(code: str) -> str:
    """认证码哈希（sha256）。"""
    import hashlib

    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def format_wage(value: float) -> str:
    """工资显示：去掉多余的尾零。"""
    return f"{value:.2f}".rstrip("0").rstrip(".")
