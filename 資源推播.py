r"""資源推播（使用者 11-21 07:27：「資訊訂閱是給你們用的，你們自己訂閱，就不用一直查，訂閱程式直接推播」）。

讀 Codex 的 resource_snapshot（logs\系統資源_最新.json），做兩件事：
1. 寫 H:\Agent共用\系統資源.md：給 AI 讀的最新 CPU／記憶體＋狀態（寬裕／吃緊／危險）＋重實驗能不能開。
2. check() 只在**狀態改變**時回一句通知（中樞貼互相討論群）；訊息含「資源」二字，訂閱了「資源」的 AI 會被派報生叫醒。
門檻：可用記憶體 < 2 GB＝吃緊、< 1 GB＝危險（使用者 07:31：平時就 3～4 GB）；顯示卡 ≥80°C、硬碟非 Healthy 也推播；CPU ≥ 85%＝忙。
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


MEM_TIGHT, MEM_DANGER = 2.0, 1.0   # 使用者 11-21 07:31：「4gb 其實是平時狀態」→ 門檻下修（平時 3～4 GB 算正常）
GPU_HOT = 80


def level(r):
    gb = (r.get("physical_available") or 0) / 2**30
    cpu = r.get("cpu")
    mem = "危險" if gb < MEM_DANGER else ("吃緊" if gb < MEM_TIGHT else "正常")
    busy = cpu is not None and cpu >= 85
    return mem, busy, gb, cpu


def health():
    """不用管理員權限讀得到的健康資訊（使用者 11-21 07:30）：顯示卡溫度（nvidia-smi）、各實體硬碟 HealthStatus。"""
    import subprocess
    out = {"gpu": None, "disks": []}
    try:
        s = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.strip().splitlines()
        if s:
            n, tmp, u = [x.strip() for x in s[0].split(",")]
            out["gpu"] = {"名稱": n, "溫度": int(tmp), "使用率": int(u)}
    except Exception:
        pass
    try:
        s = subprocess.run(["powershell", "-NoProfile", "-c", "Get-PhysicalDisk | % { $_.FriendlyName + '|' + $_.HealthStatus }"],
                           capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout
        out["disks"] = [l.strip().split("|") for l in s.splitlines() if "|" in l]
    except Exception:
        pass
    return out


def write_md(r, mem, busy, gb, cpu, hl):
    try:
        pc = open(PC, encoding="utf-8").read()
        pc_line = next((l for l in pc.splitlines() if l.startswith("- 狀態")), "- 狀態：—")
    except OSError:
        pc_line = "- 狀態：—"
    age = (time.time() - r.get("at", 0)) / 60 if r.get("at") else None
    ok = mem == "正常" and not busy and "打遊戲" not in pc_line
    g = hl.get("gpu")
    disks = hl.get("disks") or []
    L = [f"# 系統資源（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；不用自己查，狀態改變時中樞會在互相討論群推播「資源推播」）", "",
         f"- 記憶體：可用 **{gb:.1f} GB**／共 {(r.get('physical_total') or 0) / 2**30:.0f} GB → **{mem}**（這台平時就是 3～4 GB 可用）",
         f"- CPU：{'—' if cpu is None else f'{cpu:.0f}%'}" + ("（忙）" if busy else ""),
         f"- 顯示卡：" + (f"{g['名稱']} {g['溫度']}°C、使用率 {g['使用率']}%" + ("（⚠️ 過熱）" if g["溫度"] >= GPU_HOT else "") if g else "讀不到"),
         f"- 硬碟：" + ("、".join(f"{n} {h}" for n, h in disks) if disks else "讀不到"),
         "- CPU／主機板溫度、SSD 磨損：要管理員權限才讀得到（使用者決定要不要裝工具）",
         f"- 使用者電腦 {pc_line[2:]}",
         f"- 資料時間：{r.get('time', '—')}" + (f"（{age:.0f} 分鐘前）" if age is not None else ""),
         f"- **重實驗現在能開嗎：{'可以（仍要審查，走 重實驗.py）' if ok else '先不要'}**", "",
         f"門檻：可用記憶體 < {MEM_TIGHT:g} GB 吃緊、< {MEM_DANGER:g} GB 危險；CPU ≥ 85% 忙；顯示卡 ≥ {GPU_HOT}°C 過熱；硬碟不是 Healthy 就警告。想被叫醒：在自己的 H:/Agent共用/訂閱/<名字>.json 喚醒關鍵字加「資源推播」。"]
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


def check():
    """回通知字串（狀態變了）或 None。"""
    r = _read(LATEST, {})
    if not r:
        return None
    mem, busy, gb, cpu = level(r)
    hl = health()
    write_md(r, mem, busy, gb, cpu, hl)
    g = hl.get("gpu")
    hot = bool(g and g["溫度"] >= GPU_HOT)
    bad = sorted(n for n, h in (hl.get("disks") or []) if h != "Healthy")
    st = _read(STATE, {})
    now = {"mem": mem, "busy": busy, "hot": hot, "bad": bad}
    if st == now:
        return None
    json.dump(now, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    if not st:
        return None   # 第一次只記狀態，不推播
    icon = "🔴" if (mem == "危險" or bad) else ("🟡" if (mem == "吃緊" or busy or hot) else "🟢")
    parts = [f"記憶體 {mem}（可用 {gb:.1f} GB）"]
    if busy:
        parts.append(f"CPU {cpu:.0f}%（忙）")
    if g:
        parts.append(f"顯示卡 {g['溫度']}°C" + ("（過熱）" if hot else ""))
    if bad:
        parts.append("⚠️ 硬碟異常：" + "、".join(bad))
    msg = f"{icon} 資源推播：" + "，".join(parts)
    msg += ("。重實驗先別開，手上的讓它收尾；詳見 H:/Agent共用/系統資源.md" if icon != "🟢" else "。恢復正常，可以排已審查的重實驗（走 重實驗.py）。")
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
