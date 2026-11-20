r"""資源推播（使用者 11-21 07:27：「資訊訂閱是給你們用的，你們自己訂閱，就不用一直查，訂閱程式直接推播」）。

讀 Codex 的 resource_snapshot（logs\系統資源_最新.json），做兩件事：
1. 寫 H:\Agent共用\系統資源.md：給 AI 讀的最新 CPU／記憶體＋狀態（寬裕／吃緊／危險）＋重實驗能不能開。
2. check() 只在**狀態改變**時回一句通知（中樞貼互相討論群）；訊息含「資源」二字，訂閱了「資源」的 AI 會被派報生叫醒。
門檻：可用記憶體 < 4 GB＝吃緊（重實驗.py 也用這條）、< 2 GB＝危險；CPU ≥ 85%＝忙。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LATEST = os.path.join(HERE, "logs", "系統資源_最新.json")
STATE = os.path.join(HERE, "logs", "資源推播狀態.json")
OUT = r"H:\Agent共用\系統資源.md"
PC = r"H:\Agent共用\使用者電腦狀態.md"


def _read(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return d


def level(r):
    gb = (r.get("physical_available") or 0) / 2**30
    cpu = r.get("cpu")
    mem = "危險" if gb < 2 else ("吃緊" if gb < 4 else "寬裕")
    busy = cpu is not None and cpu >= 85
    return mem, busy, gb, cpu


def write_md(r, mem, busy, gb, cpu):
    try:
        pc = open(PC, encoding="utf-8").read()
        pc_line = next((l for l in pc.splitlines() if l.startswith("- 狀態")), "- 狀態：—")
    except OSError:
        pc_line = "- 狀態：—"
    age = (time.time() - r.get("at", 0)) / 60 if r.get("at") else None
    ok = mem == "寬裕" and not busy and "打遊戲" not in pc_line
    L = [f"# 系統資源（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；不用自己查，狀態改變時中樞會在互相討論群推播「資源」）", "",
         f"- 記憶體：可用 **{gb:.1f} GB**／共 {(r.get('physical_total') or 0) / 2**30:.0f} GB → **{mem}**",
         f"- CPU：{'—' if cpu is None else f'{cpu:.0f}%'}" + ("（忙）" if busy else ""),
         f"- 使用者電腦 {pc_line[2:]}",
         f"- 資料時間：{r.get('time', '—')}" + (f"（{age:.0f} 分鐘前）" if age is not None else ""),
         f"- **重實驗現在能開嗎：{'可以（仍要審查，走 重實驗.py）' if ok else '先不要'}**", "",
         "門檻：可用記憶體 < 4 GB 吃緊、< 2 GB 危險；CPU ≥ 85% 忙。想被叫醒：在自己的 H:\\Agent共用\\訂閱\\<名字>.json 喚醒關鍵字加「資源」。"]
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


def check():
    """回通知字串（狀態變了）或 None。"""
    r = _read(LATEST, {})
    if not r:
        return None
    mem, busy, gb, cpu = level(r)
    write_md(r, mem, busy, gb, cpu)
    st = _read(STATE, {})
    now = {"mem": mem, "busy": busy}
    if st == now:
        return None
    json.dump(now, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    if not st:
        return None   # 第一次只記狀態，不推播
    icon = {"寬裕": "🟢", "吃緊": "🟡", "危險": "🔴"}[mem]
    msg = f"{icon} 資源推播：記憶體 {st.get('mem')}→**{mem}**（可用 {gb:.1f} GB）" + (f"，CPU {cpu:.0f}%（忙）" if busy else "")
    if mem != "寬裕" or busy:
        msg += "。重實驗先別開，手上的讓它收尾；詳見 H:\\Agent共用\\系統資源.md"
    else:
        msg += "。可以排已審查的重實驗（走 重實驗.py）。"
    return msg


def tick():
    """中樞每 10 分鐘呼叫（掛在 資料夾入口.write 後面，不改 hub.py）：資源快照太舊就先補一筆，狀態變了就丟一張 inbox 單，中樞貼到互相討論群。"""
    r = _read(LATEST, {})
    if time.time() - r.get("at", 0) > 15 * 60:
        try:
            import importlib
            importlib.import_module("resource_snapshot").write()
        except Exception:
            pass
    msg = check()
    if msg:
        d = r"H:\Agent共用\inbox"
        name = time.strftime("%m%d-%H%M%S-資源推播.txt")
        tmp = os.path.join(d, name + ".tmp")
        open(tmp, "w", encoding="utf-8").write(f"from: 系統資源\nto: none\nroom: 互相討論\n---\n{msg}\n")
        os.replace(tmp, os.path.join(d, name))
    return msg


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(check())
    print(open(OUT, encoding="utf-8").read())
