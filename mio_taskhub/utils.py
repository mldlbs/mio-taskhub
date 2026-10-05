from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


def parse_utc(value):
    """把任意来源的时间值统一成带时区的 UTC datetime；无法解析时返回 None。

    为什么需要它：原生 SQL（`text()`）查询不走 ORM 的结果类型转换，SQLite 会把
    DATETIME 列原样返回成字符串，形如 `'2026-09-17 09:23:45.123456'`
    （存的是 UTC 挂钟时间，不带偏移量）。直接对这些值取 `.tzinfo`、调
    `.isoformat()` 或做减法都会抛异常，必须先转换。

    可接受：None / datetime / ISO 字符串 / Unix 时间戳（int、float）。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text_value = str(value).strip()
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
