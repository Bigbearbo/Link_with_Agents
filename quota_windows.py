"""Convert recorded local quotas to explicit day or month windows."""
from datetime import datetime, time, timedelta, timezone
from math import isfinite


PACE_MARGIN = 0.2   # 使用者 11-23 06:25：用量可以超過標準速度線「剩餘比例的 20%」才算超速（例：時間過 60%，剩 40%×0.2＝8%，用到 68% 才開始調整）


def pace_state(used, elapsed):
    """依使用者規則判狀態：used、elapsed 都是百分比。回傳 已達上限／超速／寬限內／正常；資料不足回 None。"""
    if used is None or elapsed is None:
        return None
    if used >= 100:
        return "已達上限"
    limit = elapsed + PACE_MARGIN * (100 - elapsed)
    if used > limit:
        return "超速"
    if used > elapsed:
        return "寬限內"
    return "正常"


def budget_window(label, used, cap, start, end, now):
    length = (end - start).total_seconds()
    elapsed = min(1.0, max(0.0, (now - start).total_seconds() / length))
    if type(cap) not in (int, float) or not isfinite(cap) or cap <= 0:
        return {"label": label, "used": None, "elapsed": round(elapsed * 100, 1),
                "speed": None, "resets": end.isoformat(), "empty_at": "", "state": "上限未知"}
    ratio = used / cap
    speed = ratio / elapsed if used > 0 and elapsed * length >= 900 else None
    empty = start + timedelta(seconds=length / speed) if speed and speed > 1 and ratio < 1 else None
    state = "已達上限" if ratio >= 1 else "待累積" if speed is None else pace_state(ratio * 100, elapsed * 100)
    return {"label": label, "used": round(ratio * 100, 1), "elapsed": round(elapsed * 100, 1),
            "speed": round(speed, 2) if speed is not None else None, "resets": end.isoformat(),
            "empty_at": empty.isoformat() if empty else "", "state": state}


def deepseek_windows(daily, monthly, daily_cap, monthly_cap, now=None):
    now = now or datetime.now().astimezone()
    day = datetime.combine(now.date(), time(), now.tzinfo)
    month = day.replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    return [budget_window("今天", daily, daily_cap, day, day + timedelta(days=1), now),
            budget_window("本月", monthly, monthly_cap, month, next_month, now)]


def nvidia_window(used, cap, now=None):
    taipei = timezone(timedelta(hours=8))
    now = (now or datetime.now(timezone.utc)).astimezone(taipei)
    utc_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = utc_start.astimezone(taipei)
    return budget_window("今天", used, cap, start, start + timedelta(days=1), now)


def estimate_from_speed(used, speed, reset, now=None):
    if used is None or speed is None or not reset:
        return ""
    try:
        end = datetime.fromisoformat(reset)
    except (TypeError, ValueError):
        return ""
    if end.tzinfo is None:
        end = end.astimezone()
    now = now or datetime.now(end.tzinfo)
    if end <= now:
        return ""
    if used >= 100:
        return "已達上限"
    if not 0 < used < 100 or speed <= 1:
        return ""
    elapsed = used / (100 * speed)
    length = (end - now).total_seconds() / (1 - elapsed)
    return (now + timedelta(seconds=length * (1 - used / 100) / speed)).isoformat()
