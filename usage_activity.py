"""Compare quota readings with completed, locally observed work."""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"H:\Agent共用")
HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "hub.log"
WATCH = Path(r"H:\Codex用\work\codex-usage\watch-state.json")
STATE = HERE / "logs" / "用量活動監測.json"
AGENTS = {"Claude": "claude", "Codex": "codex", "Gemini": "gemini", "輝達": "輝達"}
STALLED_AFTER = 30 * 60
FLAT_AFTER = 2 * 3600
FLAT_READS = 3
FLAT_ACTIVITY = 3
ACTIVITY_WINDOW = 4 * 3600
LOG_TAIL = 512 * 1024
LOG_LINE = re.compile(r"^(\d\d:\d\d:\d\d) ")
DONE = re.compile(r"^\d\d:\d\d:\d\d \[.*?\] (Claude|Codex|Gemini|輝達) (?:完成（\d+ 秒）|分身先回答了新訊息)$")


def read_dict(path):
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def timestamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return 0


def completed(lines, now, log_mtime):
    """Recover local date for the hub's time-only log, including midnight."""
    result = {name: [] for name in AGENTS}
    last = datetime.fromtimestamp(min(now, log_mtime))
    cutoff = now - ACTIVITY_WINDOW
    for line in reversed(lines):
        prefix = LOG_LINE.match(line)
        if not prefix:
            continue
        hour, minute, second = map(int, prefix[1].split(":"))
        if hour > 23 or minute > 59 or second > 59:
            continue
        when = last.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if when - last > timedelta(hours=12):
            when -= timedelta(days=1)
        last = min(last, when)
        at = when.timestamp()
        if at < cutoff:
            break
        match = DONE.fullmatch(line)
        if match and at <= now:
            result[match[1]].append(at)
    return {name: sorted(times) for name, times in result.items()}


def activity(now):
    try:
        with LOG.open("rb") as file:
            end = file.seek(0, os.SEEK_END)
            start = max(0, end - LOG_TAIL)
            file.seek(start)
            if start:
                file.readline()
            lines = file.read(LOG_TAIL).decode("utf-8", errors="replace").splitlines()
            modified = os.fstat(file.fileno()).st_mtime
    except OSError:
        return {name: [] for name in AGENTS}
    return completed(lines, now, modified)


def codex_activity(now):
    watch = read_dict(WATCH)
    at = timestamp(watch.get("activity_batch_started_at"))
    if watch.get("activity_events", 0) and now - ACTIVITY_WINDOW <= at <= now:
        return at
    return 0


def snapshot_activity(now):
    times = activity(now)
    supplemental = codex_activity(now)
    return {name: {"中樞完成數近4小時": len(events),
                   "最近活動": datetime.fromtimestamp(at).astimezone().isoformat() if at else None}
            for name, events in times.items()
            for at in [max(events[-1] if events else 0, supplemental if name == "Codex" else 0)]}


def readings(name, usage, now):
    if name == "輝達":
        calls = usage.get("今天呼叫")
        source = timestamp(usage.get("更新"))
        if type(calls) is not int or calls < 0 or not source:
            return {}
        day = (datetime.fromtimestamp(source) - timedelta(hours=8)).date()
        current_day = (datetime.fromtimestamp(now) - timedelta(hours=8)).date()
        return {"每日": [calls, day.isoformat()]} if day == current_day else {}
    result = {}
    for key in ("five_hour", "seven_day"):
        window = usage.get(key)
        if not isinstance(window, dict) or window.get("available") is False:
            continue
        used, reset = window.get("used"), timestamp(window.get("resets"))
        if type(used) in (int, float) and 0 <= used <= 100 and reset > now:
            result[key] = [used, reset]
    return result


def assess(name, usage, events, supplemental, previous, now):
    source = timestamp(usage.get("更新"))
    if source > now + 60:
        return previous, []
    current = {"source": source}
    alerts = []
    pending = [at for at in events if at > source]
    if supplemental > source:
        pending.append(supplemental)
    if previous.get("source") == source and previous.get("pending"):
        pending.append(previous["pending"])
    if pending:
        first = current["pending"] = min(pending)
        if now - first >= STALLED_AFTER:
            alerts.append((f"活動用量未更新:{AGENTS[name]}", name,
                           f"{name} 有可觀測的活動，之後至少 30 分鐘沒有新用量讀數。"))
    values = readings(name, usage, now)
    if not values or not source:
        return current, alerts
    baseline = previous.get("baseline") or {}
    if values != baseline.get("values") or source < baseline.get("last_source", 0):
        baseline = {"values": values, "since": now, "last_source": source, "reads": 1}
    elif source > baseline["last_source"]:
        baseline = dict(baseline, last_source=source, reads=baseline["reads"] + 1)
    current["baseline"] = baseline
    relevant_completions = sum(at > baseline["since"] for at in events)
    if (not alerts and now - source <= STALLED_AFTER and now - baseline["since"] >= FLAT_AFTER
            and baseline["reads"] >= FLAT_READS and relevant_completions >= FLAT_ACTIVITY):
        alerts.append((f"活動用量未變:{AGENTS[name]}", name,
                       f"{name} 有持續完成紀錄，至少 2 小時的多筆新用量讀數未變，請核對。"
                       "百分比也可能因用量低或取整而暫時不變。"))
    return current, alerts


def detect():
    now = time.time()
    previous = read_dict(STATE)
    observed = activity(now)
    supplemental = codex_activity(now)
    current, alerts = {}, []
    for name, file_key in AGENTS.items():
        usage = read_dict(ROOT / f"{file_key}-用量.json")
        current[name], found = assess(name, usage, observed[name], supplemental if name == "Codex" else 0,
                                      previous.get(name, {}), now)
        alerts.extend(found)
    if current != previous:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, STATE)
    return alerts
