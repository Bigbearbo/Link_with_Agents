r"""用量摘要給 AI 讀（使用者 11-21 01:41：「用量儀表板這種也弄成你們的版本，方便大家決定找誰，一個設定檔兩種輸出，數值同一個來源」）。

來源＝網頁用量儀表板讀的同一批檔：H:\Agent共用\{claude,codex,gemini,輝達,免費池}-用量.json（各自的更新程式寫）。
這裡只讀不寫來源，產生 H:\Agent共用\用量狀態.md：每位的額度、速度、狀態、重置時間，加一欄「派工建議」。
中樞每 10 分鐘的巡檢會呼叫 write()；也可手動 python 用量摘要.py。
"""
import json
import time

SRC = r"H:\Agent共用\{}-用量.json"
OUT = r"H:\Agent共用\用量狀態.md"


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


def ai_text():
    L = [f"# 用量狀態（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝用量儀表板；數值來源＝H:\\Agent共用\\*-用量.json）", "",
         "| 誰 | 窗口 | 已用% | 速度 | 狀態 | 重置 | 派工建議 | 資料時間 |", "|---|---|---|---|---|---|---|---|"]
    for who, win, used, speed, state, reset, upd in rows():
        L.append(f"| {who} | {win} | {'—' if used is None else used} | {'—' if speed is None else speed} | {state or '—'} | {reset or '—'} | "
                 f"{_advice(used, speed, state)} | {upd or '—'} |")
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
    L += ["", "速度＝已用% ÷ 時間已過%；>1 表示照這速度會在重置前用完。派工建議只是參考，使用者點名優先。"]
    return "\n".join(L) + "\n"


def write():
    open(OUT, "w", encoding="utf-8", newline="\n").write(ai_text())


if __name__ == "__main__":
    import sys
    write()
    sys.stdout.reconfigure(encoding="utf-8")
    print(ai_text())
