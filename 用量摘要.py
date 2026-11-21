r"""用量摘要給 AI 讀（使用者 11-21 01:41：「用量儀表板這種也弄成你們的版本，方便大家決定找誰，一個設定檔兩種輸出，數值同一個來源」）。

來源＝網頁用量儀表板讀的同一批檔：H:\Agent共用\{claude,codex,gemini,輝達,免費池}-用量.json（各自的更新程式寫）。
這裡只讀不寫來源，產生 H:\Agent共用\用量狀態.md：每位的額度、速度、狀態、重置時間，加一欄「派工建議」。
中樞每 10 分鐘的巡檢會呼叫 write()；也可手動 python 用量摘要.py。
"""
import json
import time

import usage_activity

SRC = r"H:\Agent共用\{}-用量.json"
OUT = r"H:\Agent共用\用量狀態.md"
NOTE = r"H:\Agent共用\用量註記.json"   # 使用者 11-21 01:44：註記欄處理例外（例：Codex 打算把額度用完）


def _load(name):
    try:
        return json.load(open(SRC.format(name), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _advice(used, speed, state):
    """派工建議：看已用比例與速度（速度＝已用%÷時間已過%，>1 代表照這速度會在重置前用完）。"""
    s = str(state or "")
    if used is not None and used >= 90:
        return "⛔ 快滿，別派"
    if "超速" in s or (speed is not None and speed > 1.3):
        return "⚠️ 超速，只派必要的"
    if speed is not None and speed > 1.0:
        return "🟡 略快，輕量工作"
    return "🟢 寬裕，可以派"


def rows():
    out = []
    c = _load("claude")
    if c:
        for k in ("five_hour", "seven_day"):
            w = c.get(k) or {}
            out.append(("Claude", w.get("label", k), w.get("used"), w.get("speed"), w.get("state"), w.get("resets"), c.get("更新")))
    x = _load("codex")
    if x:
        w = x.get("seven_day") or {}
        out.append(("Codex", "7天", w.get("used"), w.get("speed"), w.get("state") or x.get("判斷"), w.get("resets"), x.get("更新")))
    g = _load("gemini")
    if g:
        for k in ("five_hour", "seven_day"):
            w = g.get(k) or {}
            out.append(("Gemini", w.get("label", k), w.get("used"), w.get("speed"), w.get("state"), w.get("resets"), g.get("更新")))
    n = _load("輝達")
    if n:
        ratio = n.get("比例")
        out.append(("輝達", "今天", round(ratio * 100, 1) if ratio is not None else None, None,
                    f"{n.get('今天呼叫')}/{n.get('每日預算')} 次", "台北 08:00 換日", n.get("更新")))
    return out


def _stale(upd, hours=2):
    """資料時間超過 hours 小時沒更新 → 回「⏳ 資料過期」（使用者 11-21 01:41 後補：Codex 用量檔停在前一天）。"""
    import datetime as _dt
    s = str(upd or "")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            t = _dt.datetime.strptime(s[:n], fmt)
            return (_dt.datetime.now() - t).total_seconds() > hours * 3600
        except ValueError:
            continue
    return False


def _notes():
    """讀例外註記；過了「到期」的不顯示。"""
    try:
        d = json.load(open(NOTE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.strftime("%m-%d %H:%M")
    return {k: v for k, v in d.items() if isinstance(v, dict) and (v.get("註記") or v.get("建議"))
            and not (v.get("到期") and v["到期"] < now)}


def _age_hours(upd):
    import datetime as _dt
    s = str(upd or "")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return (_dt.datetime.now() - _dt.datetime.strptime(s[:n], fmt)).total_seconds() / 3600
        except ValueError:
            continue
    return None


def _note_cell(n, upd):
    """註記欄＝自動偵測（資料多久沒更新，使用者 11-21 01:45）＋人工註記。"""
    parts = []
    h = _age_hours(upd)
    if h is not None and h > 2:
        parts.append(f"⏳ 用量資料 {h:.0f} 小時沒更新（自動偵測）")
    if n and n.get("註記"):
        parts.append(n["註記"] + (f"（到 {n['到期']}）" if n.get("到期") else ""))
    return "；".join(parts) or "—"


def ai_text():
    L = [f"# 用量狀態（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝用量儀表板；數值來源＝H:\\Agent共用\\*-用量.json）", "",
         "| 誰 | 窗口 | 已用% | 速度 | 狀態 | 重置 | 派工建議 | 註記 | 資料時間 |", "|---|---|---|---|---|---|---|---|---|"]
    nt = _notes()
    for who, win, used, speed, state, reset, upd in rows():
        L.append(f"| {who} | {win} | {'—' if used is None else used} | {'—' if speed is None else speed} | {state or '—'} | {reset or '—'} | "
                 f"{(nt.get(who) or {}).get('建議') or ('⏳ 資料過期，數字可能偏舊' if _stale(upd) else _advice(used, speed, state))} | "
                 f"{_note_cell(nt.get(who), upd)} | {upd or '—'} |")
    g = _load("gemini")
    if g and g.get("判斷"):
        L += ["", f"- Gemini 補充：{g['判斷']}"]
    x = _load("codex")
    if x and x.get("判斷"):
        L.append(f"- Codex 補充：{x['判斷']}（Codex 額度保護見 中樞參數.md）")
    f = _load("免費池")
    if f:
        sv = f.get("服務", {})
        L.append("- 免費池今天：" + "、".join(f"{k} {v.get('今天次數', 0)}" + (f"/{v['上限']}" if v.get("上限") else "") for k, v in sv.items()))
    L += ["", "速度＝已用% ÷ 時間已過%；>1 表示照這速度會在重置前用完。派工建議只是參考，使用者點名優先。", "例外（例：打算把週額度用完、要保留額度）寫在 H:\\Agent共用\\用量註記.json 自己那列：註記／建議（會覆寫派工建議）／到期。"]
    return "\n".join(L) + "\n"


CREDIT = r"H:\Agent共用\贈點.json"   # 使用者 11-21 22:02：有期限的贈點也弄個進度條以免忘了


def credit_rows(today=None):
    """贈點列 → [{名稱, 已用, 總額, 百分比, 剩天, 預估到期前用掉, 起算註, 來源註, 判斷}]。
    速度＝已用÷已過天數（從「開始」起算；沒填就從「更新時間」起算並標註）。手動數字與免費池本機估算取較高者。"""
    import datetime as _dt
    try:
        d = json.load(open(CREDIT, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pool = ((_load("免費池") or {}).get("服務") or {})
    now = today or _dt.datetime.now()
    out = []
    for r in d.get("列") or []:
        try:
            total, used = float(r["總額"]), float(r.get("已用美元") or 0)
            end = _dt.datetime.strptime(r["到期"], "%Y-%m-%d") + _dt.timedelta(days=1)   # 到期日當天還能用
        except (KeyError, ValueError, TypeError):
            continue
        src = "手動"
        auto = (pool.get(r.get("名稱")) or {}).get("已用美元")
        if isinstance(auto, (int, float)) and auto > used:
            used, src = float(auto), "本機估算（比手動高）"
        start_s, start_note = r.get("開始"), ""
        if not start_s:
            start_s, start_note = str(r.get("更新時間") or "")[:10], "（開始日不明，從更新時間起算）"
        try:
            start = _dt.datetime.strptime(start_s, "%Y-%m-%d")
        except ValueError:
            start = None
        left = max(0.0, (end - now).total_seconds() / 86400)
        proj = None
        if start is not None:
            past = (now - start).total_seconds() / 86400
            if past >= 0.5:
                proj = used + used / past * left
        if left <= 0:
            verdict = "⌛ 已到期"
        elif proj is None:
            verdict = "— 起算不到半天，速度待算"
        elif proj < total:
            verdict = f"⚠️ 到期前用不完（照速度剩約 ${total - proj:.2f} 會浪費）"
        else:
            verdict = "🟢 正常（照速度到期前會用完）"
        out.append({"名稱": r.get("名稱", "?"), "已用": used, "總額": total, "百分比": round(100 * used / total, 1) if total else None,
                    "剩天": left, "預估": proj, "起算註": start_note, "來源註": src, "到期": r["到期"],
                    "更新": r.get("更新時間") or "—", "判斷": verdict})
    return out


def credit_text():
    cs = credit_rows()
    if not cs:
        return ""
    L = ["", "## 贈點（有期限）", "", "| 名稱 | 已用/總額 | 進度 | 到期（剩幾天） | 照速度到期前會用掉 | 判斷 | 資料 |", "|---|---|---|---|---|---|---|"]
    for c in cs:
        pct = c["百分比"] or 0
        bar = "█" * int(round(pct / 10)) + "░" * (10 - int(round(pct / 10)))
        proj = "—" if c["預估"] is None else f"約 ${c['預估']:.2f}{c['起算註']}"
        L.append(f"| {c['名稱']} | ${c['已用']:.2f}/${c['總額']:.0f} | {bar} {pct}% | {c['到期']}（剩 {c['剩天']:.0f} 天） | {proj} | {c['判斷']} | {c['來源註']}；手動更新 {c['更新']} |")
    L.append("")
    L.append("來源＝H:\\Agent共用\\贈點.json（使用者或 AI 看帳單頁手動更新），免費池本機估算較高時取高者。")
    return "\n".join(L) + "\n"


HIST = r"H:\Agent共用\用量歷史.jsonl"   # 使用者 11-21 06:33：用量加時間快照（每小時一筆），之後可做圖表或看近期變化


CODEX_USAGE = r"H:\Agent共用\codex-用量\codex_usage.py"


def refresh_codex_if_due(min_gap):
    """活動監看閒置或到期時，仍在小時快照前補讀 Codex 額度。"""
    import subprocess
    import sys

    age = _age_hours((_load("codex") or {}).get("更新"))
    if age is not None and 0 <= age * 3600 < min_gap:
        return
    try:
        subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", CODEX_USAGE, "update"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        # 用量來源保留最後一次成功讀數及它的時間。
        pass


def snapshot(min_gap=3300):
    """距上一筆超過 min_gap 秒才寫一筆：{時間, 列:[[誰,窗口,已用%,速度,狀態,重置時間,來源時間,過期]...]}。中樞每 10 分鐘呼叫，實際約每小時一筆。"""
    import os
    try:
        if os.path.exists(HIST) and time.time() - os.path.getmtime(HIST) < min_gap:
            return False
    except OSError:
        pass
    refresh_codex_if_due(min_gap)
    rec = {"時間": time.strftime("%Y-%m-%d %H:%M"),
           "活動": usage_activity.snapshot_activity(time.time()),
           "列": [[w, win, u, s, st, reset, upd, _stale(upd) if upd else None] for w, win, u, s, st, reset, upd in rows()]}
    with open(HIST, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def recent(hours=6):
    """最近幾筆快照 → {(誰,窗口): [已用%...]}，給 ai_text 畫「近期變化」。"""
    try:
        lines = open(HIST, encoding="utf-8").read().splitlines()[-hours:]
    except OSError:
        return {}, []
    out, times = {}, []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        times.append(r["時間"][11:16])
        for row in r["列"]:
            w, win, u = row[:3]
            if len(row) < 8 or not row[6]:
                u = "—（來源時間未記錄）"
            elif row[7]:
                u = "—（來源過期）"
            out.setdefault((w, win), []).append(u)
    return out, times


def write():
    snapshot()
    t = ai_text() + credit_text()
    h, times = recent()
    if times:
        t += f"\n## 近期變化（每小時快照，{times[0]}～{times[-1]}；完整紀錄 H:\\Agent共用\\用量歷史.jsonl）\n\n"
        t += "\n".join(f"- {w} {win}：" + " → ".join("—" if v is None else str(v) for v in vs) for (w, win), vs in h.items()) + "\n"
    open(OUT, "w", encoding="utf-8", newline="\n").write(t)


if __name__ == "__main__":
    import sys
    write()
    sys.stdout.reconfigure(encoding="utf-8")
    print(ai_text() + credit_text())
