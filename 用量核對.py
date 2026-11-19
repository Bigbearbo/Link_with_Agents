"""check：核對網頁用量條顯示的數字能不能對上來源；對不上自動點名負責人報錯。
使用者 11-19 21:47：「我覺得我需要一個 check 指令，自動核對用量顯示能不能對上，錯了自動找負責人傳報錯，就叫 check 好了，大小寫全通，打在開頭。」

每家核對兩件事：①顯示的數字＝來源檔的數字 ②來源檔夠新（不是停在很久以前）。另外各家有自己的對帳：
  輝達：OpenCode 紀錄重算 vs 顯示；DeepSeek：OpenRouter 金鑰今天用量 vs 本機帳本（ds.audit）。
  python 用量核對.py      在終端機跑一次（不發群組）
"""
import json, os, time

SHARED = r"H:\Agent共用"
OWNER = {"Claude": "Claude", "Codex": "Codex", "Gemini": "Gemini", "輝達": "Claude", "DeepSeek": "Claude"}   # 用量檔誰負責更新
FILES = {"Claude": "claude-用量.json", "Codex": "codex-用量.json", "Gemini": "gemini-用量.json"}
FRESH_MIN = {"Claude": 90, "Codex": 180, "Gemini": 90}   # 超過這麼久沒更新就算停擺


def check(summary):
    """summary＝hub.usage_summary() 的結果（網頁顯示的那份）。回傳 [(狀態, 名字, 說明, 負責人)]，狀態 ok／warn／bad。"""
    shown = {a["name"]: a for a in summary.get("agents", [])}
    out = []
    for name, fn in FILES.items():
        p = os.path.join(SHARED, fn)
        a = shown.get(name, {})
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            out.append(("bad", name, f"用量檔讀不到（{type(e).__name__}）", OWNER[name]))
            continue
        age = (time.time() - os.path.getmtime(p)) / 60
        resets = float((d.get("seven_day") or {}).get("resets_at_unix") or 0)
        if age > FRESH_MIN[name]:
            note = "，而且已經過了重置時間，數字一定是舊的" if resets and time.time() > resets else ""
            out.append(("bad", name, f"用量檔 {age / 60:.1f} 小時沒更新（最後 {str(d.get('更新'))[:16]}）{note}", OWNER[name]))
            continue
        src = {lbl: (d.get(k) or {}).get("used") for k, lbl in (("five_hour", "5小時"), ("seven_day", "7天"))}
        wins = {w["label"]: w.get("used") for w in a.get("windows", [])}
        diff = [f"{lbl} 顯示 {wins.get(lbl)}、檔案 {v}" for lbl, v in src.items()
                if v is not None and wins.get(lbl) is not None and abs(float(wins[lbl]) - float(v)) > 0.6]
        if a.get("missing"):
            out.append(("bad", name, "網頁沒顯示（讀檔失敗）", "Claude"))
        elif diff:
            out.append(("bad", name, "顯示和檔案對不上：" + "；".join(diff), "Claude"))
        else:
            out.append(("ok", name, f"對得上（{age:.0f} 分鐘前更新）", OWNER[name]))
    try:   # 輝達：用 OpenCode 紀錄重算一次，跟顯示比
        import window_agent as wa
        used = wa.nvidia_total_today()[0]
        cnt = next((w.get("count") for w in shown.get("輝達", {}).get("windows", [])), "")
        if cnt and cnt.split("/")[0] != str(used):
            out.append(("bad", "輝達", f"顯示 {cnt}，重算 {used}", "Claude"))
        else:
            out.append(("ok", "輝達", f"對得上（今天 {used} 次；以 OpenCode 紀錄為準，網站數字請你偶爾手動看一眼）", "Claude"))
    except Exception as e:
        out.append(("bad", "輝達", f"重算失敗（{type(e).__name__}）", "Claude"))
    try:   # 免費池（Codex 01:37 補漏）：顯示的今天次數 vs 帳本重數；沒有服務端資料的標「未驗證」，不推論付費
        import sys
        sys.path.insert(0, os.path.join(SHARED, "ai-call"))
        import importlib
        pool = importlib.import_module("免費AI池")
        led = pool.ledger_counts()
        u = json.load(open(os.path.join(SHARED, "免費池-用量.json"), encoding="utf-8"))
        wins = {w["label"].replace("（贈點）", ""): w for w in shown.get("免費池", {}).get("windows", [])}
        today = time.strftime("%Y-%m-%d", time.gmtime())
        for n, s in u.get("服務", {}).items():
            real = led.get(n, 0)
            cnt = str((wins.get(n) or {}).get("count", ""))
            disp = int(cnt.split(" 次")[0]) if cnt.split(" 次")[0].isdigit() else real   # 贈點欄顯示金額，不比次數
            if n not in wins:
                out.append(("bad", f"免費池/{n}", "用量檔有、網頁沒顯示", "Claude"))
            elif disp != real:
                out.append(("bad", f"免費池/{n}", f"顯示今天 {disp} 次、帳本重數 {real} 次", "Claude"))
            else:
                src = "服務回報剩餘" if s.get("來源") == "服務回報" else ("本機帳本估花費" if s.get("贈點") else "未驗證（服務沒回報剩餘，只有本機計數）")
                out.append(("ok" if s.get("來源") == "服務回報" else "warn", f"免費池/{n}", f"今天 {real} 次對得上；{src}", "Claude"))
    except Exception as e:
        out.append(("warn", "免費池", f"對帳沒跑成（{type(e).__name__}）", "Claude"))
    try:   # DeepSeek：OpenRouter 金鑰的今天用量 vs 本機帳本
        import sys
        sys.path.insert(0, os.path.join(SHARED, "ai-call", "deepseek"))
        import ds
        r = ds.audit()
        if abs(r.get("差額") or 0) > 0.01:
            out.append(("bad", "DeepSeek", f"OpenRouter 說今天（UTC）${r['金鑰今天UTC']}，本機帳本 ${r['帳本今天UTC']}，差 ${r['差額']}", "Claude"))
        else:
            out.append(("ok", "DeepSeek", f"對得上（OpenRouter ${r['金鑰今天UTC']}＝帳本 ${r['帳本今天UTC']}）", "Claude"))
    except Exception as e:
        out.append(("warn", "DeepSeek", f"對帳沒跑成（{type(e).__name__}），網頁顯示的是本機帳本", "Claude"))
    return out


def report(rows):
    mark = {"ok": "✅", "warn": "⚠️", "bad": "❌"}
    lines = ["🔎 check 用量核對（" + time.strftime("%H:%M") + "）"] + [f"{mark[s]} {n}：{t}" for s, n, t, _ in rows]
    bad = [(n, t, o) for s, n, t, o in rows if s == "bad"]
    if bad:
        lines.append("")
        for owner in sorted({o for _, _, o in bad}):
            items = "；".join(f"{n} {t}" for n, t, o in bad if o == owner)
            lines.append(f"@{owner} 用量核對有錯，請修：{items}")
    else:
        lines.append("全部對得上。")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.argv = ["x"]
    import importlib.util as u
    s = u.spec_from_file_location("hubm", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub.py"))
    m = u.module_from_spec(s)
    s.loader.exec_module(m)
    print(report(check(m.usage_summary())))
