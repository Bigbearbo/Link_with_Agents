"""Detect Codex quota that stays stale despite observed session activity."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

USAGE = Path(r"H:\Agent共用\codex-用量.json")
WATCH = Path(r"H:\Codex用\work\codex-usage\watch-state.json")
STATE = Path(__file__).resolve().parent / "logs" / "codex-用量活動.json"
STALE_GRACE = 30 * 60
FLAT_GRACE = 2 * 3600
FLAT_MIN_READS = 3
FLAT_MIN_ACTIVITY = 12


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def stamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return 0


def windows(usage, now):
    result = {}
    for key in ("five_hour", "seven_day"):
        window = usage.get(key) or {}
        used = window.get("used")
        reset = stamp(window.get("resets"))
        if window.get("available") and isinstance(used, (int, float)) and 0 <= used <= 100 and reset > now:
            result[key] = [used, reset]
    return result


def assess(usage, watch, previous, now):
    """Return the next observation and any reportable anomaly."""
    source = stamp(usage.get("更新"))
    started = stamp(watch.get("started_at"))
    batch = stamp(watch.get("activity_batch_started_at"))
    events = watch.get("activity_events")
    if not started or type(events) is not int or events < 0 or source > now + 60:
        return {}, []
    same_watch = previous.get("watch_started") == started and events >= previous.get("events", 0)
    current = {"watch_started": started, "events": events, "source": source}
    stalled = []
    if events and batch > source and batch <= now:
        pending = batch
        if same_watch and previous.get("source") == source:
            pending = min(batch, previous.get("pending", batch))
        current["pending"] = pending
        if now - pending >= STALE_GRACE:
            stalled.append(("活動用量未更新:codex", "Codex",
                            "Codex 有活動，但之後至少 30 分鐘沒有新的用量讀數。請檢查額度讀取器。"))
    current_windows = windows(usage, now)
    baseline = previous.get("baseline") if same_watch else None
    if not current_windows or not source:
        return current, stalled
    if not baseline or current_windows != baseline.get("windows") or source < baseline.get("last_source", 0):
        baseline = {"windows": current_windows, "since": now, "events": events, "last_source": source, "reads": 1}
    elif source > baseline["last_source"]:
        baseline = dict(baseline, last_source=source, reads=baseline["reads"] + 1)
    current["baseline"] = baseline
    if (not stalled and now - source <= STALE_GRACE and now - baseline["since"] >= FLAT_GRACE
            and baseline["reads"] >= FLAT_MIN_READS and events - baseline["events"] >= FLAT_MIN_ACTIVITY):
        stalled.append(("活動用量未變:codex", "Codex",
                        "Codex 持續有活動，至少 2 小時內 3 次以上的新額度讀數都相同。"
                        "請核對用量條；也可能只是用量很低或顯示取整。"))
    return current, stalled


def detect():
    previous = read_json(STATE)
    current, alerts = assess(read_json(USAGE), read_json(WATCH), previous, time.time())
    if current != previous:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, STATE)
    return alerts
