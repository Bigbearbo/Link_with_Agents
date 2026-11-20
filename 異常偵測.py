r"""系統異常偵測 v1（使用者 11-21 01:45：「監測用量更新，兩小時沒更新就顯示在註記，可以開始打造系統報錯處理流程」）。

既有的三條：報錯接線生（使用者用白話報錯）、求救（AI 自己卡住喊救命）、流程體檢（每天算指標）。
缺的是「**沒人喊、但機器看得出來壞了**」的那一類 → 這支每 10 分鐘巡一次（中樞 shared_guard_loop 呼叫 check()）：
  1. 用量資料過期：H:\Agent共用\*-用量.json 超過 2 小時沒更新。
  2. AI 呼叫失敗：hub.log 最近 1 小時「失敗」行（例：輝達 503 服務過載），同一位 ≥3 次。
  3. 權限一直卡著：最近 1 小時同一個權限請求被通知 ≥3 次（例：輝達要讀 三方討論\*）。
  4. 給 AI 讀的摘要刷新失敗。
每條異常有固定「異常碼」，狀態存 logs\異常狀態.json：
  新出現 → 記錄＋寫 H:\Agent共用\系統異常.md＋（回傳）一行通知，由中樞貼到互相討論群（不吵主討論）；
  持續中 → 只更新次數，不重複通知（每 6 小時提醒一次）；
  消失 → 標「已恢復」，記錄從出現到恢復多久（之後量系統磨合速度）。
負責人照 團隊職責：用量檔→該 AI 自己；中樞、權限→Claude；其他→Claude 分派。
"""
import glob
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "異常狀態.json")
LOG = os.path.join(HERE, "logs", "hub.log")
OUT = r"H:\Agent共用\系統異常.md"
REMIND_SEC = 6 * 3600
OWNER = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "輝達": "輝達", "免費池": "Claude"}


def _usage_stale():
    found = []
    for f in glob.glob(r"H:\Agent共用\*-用量.json"):
        who = os.path.basename(f).replace("-用量.json", "")
        age = (time.time() - os.path.getmtime(f)) / 3600
        if age > 2:
            found.append((f"用量過期:{who}", OWNER.get(who, "Claude"),
                          f"{who} 的用量檔 {age:.0f} 小時沒更新（{f}）；用量摘要與派工建議會偏舊。"))
    return found


def _recent_log_lines(hours=1):
    try:
        lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()[-4000:]
    except OSError:
        return []
    now = time.localtime()
    cut = time.time() - hours * 3600
    out = []
    for ln in lines:
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", ln)
        if not m:
            continue
        t = time.mktime(now[:3] + (int(m.group(1)), int(m.group(2)), int(m.group(3))) + now[6:])
        if t > time.time() + 60:      # 跨午夜：昨天的
            t -= 86400
        if t >= cut:
            out.append(ln)
    return out


def _call_failures(lines):
    cnt, sample = {}, {}
    for ln in lines:
        m = re.search(r"\] (\S+) 失敗：(.*)", ln)
        if m:
            who = m.group(1)
            cnt[who] = cnt.get(who, 0) + 1
            sample[who] = m.group(2)[:120]
    return [(f"呼叫失敗:{w}", "Claude", f"{w} 最近 1 小時呼叫失敗 {n} 次；最近一次：{sample[w]}")
            for w, n in cnt.items() if n >= 3]


def _permission_stuck(lines):
    cnt = {}
    for ln in lines:
        m = re.search(r"權限請求：(\S+) (.{0,80})", ln)
        if m:
            k = (m.group(1), m.group(2)[:60])
            cnt[k] = cnt.get(k, 0) + 1
    return [(f"權限卡住:{w}:{what[:20]}", "Claude", f"{w} 的權限請求最近 1 小時重複 {n} 次還沒處理：{what}（要使用者決定）")
            for (w, what), n in cnt.items() if n >= 3]


def _summary_fail(lines):
    bad = [ln for ln in lines if "給 AI 讀的摘要刷新失敗" in ln]
    return [("摘要刷新失敗", "Claude", f"給 AI 讀的摘要刷新失敗 {len(bad)} 次；最近：{bad[-1][:120]}")] if bad else []


def detect():
    lines = _recent_log_lines()
    return _usage_stale() + _call_failures(lines) + _permission_stuck(lines) + _summary_fail(lines)


def check():
    """巡一次；回傳要通知的文字（沒有新事就回 None）。"""
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    now = time.time()
    found = {code: (owner, msg) for code, owner, msg in detect()}
    notes = []
    for code, (owner, msg) in found.items():
        s = st.get(code)
        if not s or s.get("狀態") == "已恢復":
            st[code] = {"狀態": "異常中", "負責": owner, "說明": msg, "出現": now, "最後": now, "次數": 1, "提醒": now}
            notes.append(f"🆕 [{code}] 負責 {owner}：{msg}")
        else:
            s.update(說明=msg, 最後=now, 次數=s.get("次數", 1) + 1)
            if now - s.get("提醒", 0) > REMIND_SEC:
                s["提醒"] = now
                notes.append(f"⏰ 仍未解決 [{code}]（已 {(now - s['出現']) / 3600:.0f} 小時）負責 {owner}：{msg}")
    for code, s in st.items():
        if code not in found and s.get("狀態") == "異常中":
            s.update(狀態="已恢復", 恢復=now)
            notes.append(f"✅ 已恢復 [{code}]（歷時 {(now - s['出現']) / 60:.0f} 分鐘）")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _write_md(st)
    return "\n".join(["🩺 系統異常巡檢"] + notes) if notes else None


def _write_md(st):
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "—"
    act = [(c, s) for c, s in st.items() if s.get("狀態") == "異常中"]
    done = sorted([(c, s) for c, s in st.items() if s.get("狀態") == "已恢復"], key=lambda x: -x[1].get("恢復", 0))[:10]
    L = [f"# 系統異常（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；中樞每 10 分鐘巡檢）", "",
         f"## 異常中（{len(act)}）"]
    L += [f"- [{c}] 負責 {s['負責']}｜出現 {fmt(s['出現'])}｜次數 {s['次數']}｜{s['說明']}" for c, s in act] or ["- 無"]
    L += ["", "## 最近恢復"]
    L += [f"- [{c}] {fmt(s['出現'])} → {fmt(s.get('恢復'))}" for c, s in done] or ["- 無"]
    L += ["", "處理：負責人修好後不用手動關，下一次巡檢偵測不到就自動標已恢復。要人決定的（權限、花錢）會寫明。"]
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(check() or "（沒有新變化）")
    print(open(OUT, encoding="utf-8").read())
