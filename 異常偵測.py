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
import sys
import time

import usage_activity


def _P(名, 預設):
    """讀團隊參數（共用區 團隊參數.json）；載入失敗就用原值，不能讓中樞因此壞掉。"""
    try:
        import importlib.util as _u
        _m = sys.modules.get("團隊參數")
        if _m is None:
            _s = _u.spec_from_file_location("團隊參數", r"H:\Agent共用\專案\團隊系統\團隊參數.py")
            _m = _u.module_from_spec(_s)
            _s.loader.exec_module(_m)
            sys.modules["團隊參數"] = _m
        return _m.get(名, 預設)
    except Exception:
        return 預設


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "異常狀態.json")
LOG = os.path.join(HERE, "logs", "hub.log")
OUT = r"H:\Agent共用\系統異常.md"
REMIND_SEC = _P("異常提醒間隔小時", 6) * 3600
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


RESIDENT_JSON = "H:/Agent共用/常駐檢查.json"   # 工單 #260：常駐檢查.py（每10分.py 最後跑）的結果


def _resident_missing():
    """常駐漏跑:<名稱>：常駐檢查.json 裡 ❌ 的項目，負責人用清單的負責欄；結果檔本身 30 分沒更新也算。"""
    try:
        age = time.time() - os.path.getmtime(RESIDENT_JSON)
        d = json.load(open(RESIDENT_JSON, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if age > 1800:
        return [("常駐漏跑:常駐檢查", "Claude", f"常駐檢查結果 {age / 60:.0f} 分鐘沒更新（每10分.py 沒在跑？）；重開後漏跑的程式沒人在看。")]
    return [(f"常駐漏跑:{r['名稱']}", r.get("負責", "Claude"),
             f"{r.get('說明', '')}；影響：{r.get('影響', '')}；重開：{r.get('重開指令', '')}"[:400])
            for r in d.get("項目", []) if r.get("狀態") == "❌"]


STALL_HOURS = _P("工單停滯小時", 6)     # 使用者 11-23 01:03：產線「卡住？」→ 異常系統 → 通知 Claude 干預；規則見 H:\Agent共用\專案\團隊系統\工單停滯干預.md
STALL_ESCALATE_HOURS = _P("停滯升級小時", 24)
WAIT_STALL_HOURS = _P("等待停滯小時", 24)  # 狀態「等」的單


def _stalled_tickets():
    """工單停滯:#N：「進行中／等」的工單，最後一筆紀錄超過 6 小時。負責干預的是 Claude；訊息裡寫原負責人和建議動作。"""
    try:
        todos = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out, now = [], time.time()
    for t in todos:
        if t.get("狀態") not in ("進行中", "等"):
            continue
        rec = t.get("紀錄") or []
        last = str(rec[-1])[:11] if rec else str(t.get("建立", ""))[:11]
        try:
            lt = time.mktime(time.strptime(time.strftime("%Y-") + last, "%Y-%m-%d %H:%M"))
        except ValueError:
            continue
        if lt > now + 86400:   # 跨年：MM-DD 比今天晚很多就是去年
            lt = time.mktime(time.strptime(str(int(time.strftime("%Y")) - 1) + "-" + last, "%Y-%m-%d %H:%M"))
        h = (now - lt) / 3600
        # 使用者 11-23 12:2x：「領了 2 小時沒做出成果就找你回報抉擇，可以合作或再拆分」——最後一筆是「領」、之後沒有做／完
        if rec and str(rec[-1])[12:14].startswith("領") and h >= _P("領單無成果小時", 2) and t.get("狀態") == "進行中":
            who = t.get("負責") or "（沒人）"
            out.append((f"領單無成果:#{t['id']}", "Claude",
                        f"#{t['id']}（負責 {who}）領了 {h:.1f} 小時還沒有進度或成果：{str(t.get('標題', ''))[:60]}｜建議：Claude 問負責人卡在哪 → 合作（加一位）／拆成小單／改派"))
            continue
        waiting = t.get("狀態") == "等"
        if waiting:   # 使用者 11-23 06:28：預期該很快有反應（簡單任務）卻沒等到 → 異常通知；「等」紀錄寫「預期 N 分」
            m = re.search(r"預期\s*(\d+)\s*分", str(rec[-1]) if rec else "")
            if m:
                lim = max(_P("預期逾時倍數", 2) * int(m.group(1)), _P("預期逾時最少分", 10)) / 60
                if lim <= h < WAIT_STALL_HOURS:
                    who = t.get("負責") or "（沒人）"
                    out.append((f"預期回應逾時:#{t['id']}", "Claude",
                                f"#{t['id']}（負責 {who}）預期 {m.group(1)} 分內有回應，已等 {h * 60:.0f} 分：{str(rec[-1])[12:100]}｜建議：Claude 查對方是否在線（心跳、權限卡住、額度）→ 代為叫醒或改派"))
                    continue
        if h < (WAIT_STALL_HOURS if waiting else STALL_HOURS):   # 等待是正常的：「等」門檻 24 小時（#272）
            continue
        who = t.get("負責") or "（沒人）"
        if waiting:
            lw = next((str(r) for r in reversed(rec) if str(r)[12:14].startswith("等")), "")
            out.append((f"工單停滯:#{t['id']}", "Claude",
                        f"#{t['id']}（負責 {who}，等）等誰太久：{lw[12:90] or '（沒寫等誰）'}（{h:.0f} 小時）｜建議：Claude：催被等的人／改派／改成不等"))
            continue
        act = (f"超過 {STALL_ESCALATE_HOURS:g} 小時：改派或退單（待辦.py 退）" if h >= STALL_ESCALATE_HOURS else
               "先看是否其實做完（有檔／有 commit → 代結）；在等人 → 改成「等」並寫等誰；否則派報提醒負責人")
        if "使用者" in str(who):
            act = "負責人是使用者：放進訊息版「給你」問要不要結或改派，不催"
        out.append((f"工單停滯:#{t['id']}", "Claude",
                    f"#{t['id']}（負責 {who}，{t.get('狀態')}）{h:.0f} 小時沒紀錄：{str(t.get('標題', ''))[:60]}｜建議：{act}"))
    return out


def _long_working():
    """工作過久:<名字>：某個 AI 連續「工作中」超過 N 分（團隊參數「工作中過久分」，預設 30）。
    使用者 11-23 22:5x：「輝達 工作中 · 231:53 這個也加入監測，設置 30 分鐘異常，參數化」。
    只在中樞程序裡跑得到（從 __main__ 拿 hub）；外部手動執行時回空。"""
    h = getattr(sys.modules.get("__main__"), "hub", None)
    if h is None or not hasattr(h, "rooms"):
        return []
    lim = _P("工作中過久分", 30) * 60
    now, out, seen = time.time(), [], set()
    for room in list(h.rooms.values()):
        try:
            items = [(a.name, now - (a.started or now)) for a in room.agents() if a.busy]
            items += [(w["who"], w.get("elapsed") or 0) for w in room.window_progress()]
        except Exception:
            continue
        for name, sec in items:
            if sec >= lim and name not in seen:
                seen.add(name)
                out.append((f"工作過久:{name}", "Claude",
                            f"{name} 已經連續工作中 {int(sec // 60)} 分鐘（門檻 {int(lim // 60)} 分，房間 {room.name}）｜"
                            "建議：Claude 看它在跑什麼——長實驗就請它回報進度與預計結束；重複舊工作或卡住就叫停、改派"))
    return out


def detect():
    lines = _recent_log_lines()
    return (_usage_stale() + usage_activity.detect() + _call_failures(lines) + _permission_stuck(lines) + _summary_fail(lines)
            + _resident_missing() + _stalled_tickets() + _long_working())


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
        if code.startswith("回報:"):
            continue   # 人工回報的不會自動消失，要有人標「已修」
        if code not in found and s.get("狀態") == "異常中":
            s.update(狀態="已恢復", 恢復=now)
            notes.append(f"✅ 已恢復 [{code}]（歷時 {(now - s['出現']) / 60:.0f} 分鐘）")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _write_md(st)
    try:   # 使用者 11-23 11:2x：卡住要留 LOG 供之後修錯、研究怎麼不卡 → 新出現的卡住類異常記進 卡住紀錄
        import importlib.util as _iu
        _sp = _iu.spec_from_file_location("卡住紀錄", r"H:\Agent共用\專案\團隊系統\卡住紀錄.py")
        _km = _iu.module_from_spec(_sp); _sp.loader.exec_module(_km)
        for n in notes:
            if not n.startswith("🆕"):
                continue
            m = re.search(r"\[(權限卡住|工單停滯|預期回應逾時|常駐漏跑|領單無成果|工作過久):([^\]]*)\]", n)
            if not m:
                continue
            kind = {"權限卡住": "權限", "工單停滯": "等人太久" if "等誰太久" in n else "沒進度",
                    "預期回應逾時": "等人太久", "常駐漏跑": "環境", "領單無成果": "沒進度", "工作過久": "沒進度"}[m.group(1)]
            who = re.search(r"（負責 ([^，）]+)", n)
            who = who.group(1) if who else m.group(2).split(":")[0] if m.group(1) == "權限卡住" else "?"
            tk = re.search(r"#(\d+)", m.group(2))
            _km.add(who, kind, n[:400], int(tk.group(1)) if tk else None)
    except Exception:
        pass
    stall = [n for n in notes if ("[工單停滯:" in n or "[預期回應逾時:" in n or "[領單無成果:" in n or "[工作過久:" in n) and not n.startswith("✅")]   # 已恢復的不叫醒（11-23 08:52 誤叫）
    if stall:   # 使用者 11-23 01:03：工單卡住 → 通知 Claude 干預（走派報生的叫醒請求，一次巡檢只叫一次）
        try:
            with open(os.path.join(HERE, "logs", "派報", "叫醒請求.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"時間": time.strftime("%m-%d %H:%M"), "給": "Claude", "原因": "工單停滯",
                                    "內容": f"{len(stall)} 張工單停滯，照 H:\\Agent共用\\專案\\團隊系統\\工單停滯干預.md 處理：" + "；".join(s[:80] for s in stall[:5])},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
    return "\n".join(["🩺 系統異常巡檢"] + notes) if notes else None


def _write_md(st):
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "—"
    act = [(c, s) for c, s in st.items() if s.get("狀態") == "異常中"]
    done = sorted([(c, s) for c, s in st.items() if s.get("狀態") == "已恢復"], key=lambda x: -x[1].get("恢復", 0))[:10]
    L = [f"# 系統異常（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；中樞每 10 分鐘巡檢）", "",
         f"## 異常中（{len(act)}）"]
    L += [f"- [{c}] 負責 {s['負責']}｜認領 {s.get('認領') or '（沒人）'}｜出現 {fmt(s['出現'])}｜次數 {s['次數']}｜{s['說明']}" for c, s in act] or ["- 無"]
    L += ["", "## 最近恢復"]
    L += [f"- [{c}] {fmt(s['出現'])} → {fmt(s.get('恢復'))}" for c, s in done] or ["- 無"]
    L += ["", "處理：自動偵測的，修好後下一次巡檢偵測不到就自動標已恢復；人工回報的（[回報:N]）要有人標已修。要人決定的（權限、花錢）會寫明。",
          "AI 操作（在中樞資料夾執行，或用網頁「報錯」頁）：",
          "  python 異常偵測.py 回報 <你的名字> <哪裡怪、怎麼重現>",
          "  python 異常偵測.py 認領 <異常碼> <你的名字>",
          "  python 異常偵測.py 已修 <異常碼> <你的名字> <怎麼修的／記錄點>"]
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _write_md(st)


def report(who, text):
    """人工回報（使用者 11-21 01:48：「發現哪裡怪提交到報錯系統」）。回傳異常碼。"""
    st = _load()
    n = 1 + max([int(c.split(":")[1]) for c in st if c.startswith("回報:") and c.split(":")[1].isdigit()] or [0])
    code = f"回報:{n}"
    now = time.time()
    st[code] = {"狀態": "異常中", "負責": "待分派", "說明": f"{who} 回報：{str(text)[:500]}", "出現": now, "最後": now,
                "次數": 1, "提醒": now, "回報者": who}
    _save(st)
    return code


def claim(code, who):
    st = _load()
    if code not in st or st[code].get("狀態") != "異常中":
        return False
    st[code]["認領"] = who
    st[code]["認領時間"] = time.time()
    if st[code].get("負責") == "待分派":
        st[code]["負責"] = who
    _save(st)
    return True


def resolve(code, who, note=""):
    st = _load()
    if code not in st:
        return False
    st[code].update(狀態="已恢復", 恢復=time.time(), 修好者=who, 修法=str(note)[:300])
    _save(st)
    return True


# ---- 工單（使用者 11-21 22:37：「報錯那個按鈕幫我變成工單+報錯，工單放前面，給我輸入的介面，還有留言選項」）----
TODO_PY = r"H:\Agent共用\待辦\待辦.py"   # 測試時把 _td().DB / _td().LOCK 換成複本
ASSIGNEES = ["自動派", "Claude", "Codex", "Gemini", "輝達", "DeepSeek"]
_TD = None


def _td():
    """載入共用待辦模組（只載一次）；用它的 load／save（save 已有 PermissionError 重試）與檔案鎖。"""
    global _TD
    if _TD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("待辦_共用", TODO_PY)
        _TD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TD)
    return _TD


def _todo_edit(fn):
    """在待辦檔案鎖內 讀→改→存；fn(d) 回傳結果。"""
    td = _td()
    td.lock()
    try:
        d = td.load()
        out = fn(d)
        td.save(d)
        return out
    finally:
        if os.path.exists(td.LOCK):
            os.remove(td.LOCK)


def ticket_new(who, title, desc="", pri="中", to="自動派", level=""):
    title = str(title).strip()[:200]
    if not title:
        return {"ok": False, "error": "標題必填"}
    pri = pri if pri in ("高", "中", "低") else "中"
    to = to if to in ASSIGNEES else "自動派"
    td = _td()

    def fn(d):
        t = {"id": max([x["id"] for x in d], default=0) + 1,
             "標題": f"{title}（來源：網頁 {td.now()} {who}）",
             "負責": "未定" if to == "自動派" else to, "優先": pri, "專案": "", "狀態": "待做",
             "建立": td.now(), "紀錄": [],
             "開單人": "使用者（網頁）" if who == "使用者" else who,
             "討論概要": f"使用者在網頁開單：{(str(desc).strip() or title)[:60]}" if who == "使用者" else f"{who} 在網頁開單：{(str(desc).strip() or title)[:60]}"}
        if str(desc).strip():
            t["說明"] = str(desc).strip()[:2000]
            t["紀錄"].append(f"{td.now()} 說明（{who}）：{t['說明'][:300]}")
        if level in td.LEVELS:
            t["難度"], t["難度來源"] = level, f"{who} 定"
        d.append(t)
        return t
    t = _todo_edit(fn)
    return {"ok": True, "id": t["id"],
            "notice": f"📝 新工單 #{t['id']}（{who}）：{title[:150]}——派工建議下一輪會排"}


def ticket_comment(who, tid, text):
    text = str(text).strip()[:1000]
    if not text:
        return {"ok": False, "error": "留言是空的"}
    td = _td()

    def fn(d):
        for t in d:
            if t["id"] == int(str(tid).lstrip("#")):
                # 只記一筆紀錄，不像「做」那樣把狀態改成進行中（還沒人領的單不該被留言弄成進行中）
                t.setdefault("紀錄", []).append(f"{td.now()} 留言（{who}）：{text}")
                return t
        return None
    try:
        t = _todo_edit(fn)
    except ValueError:
        return {"ok": False, "error": "編號不對"}
    if not t:
        return {"ok": False, "error": f"沒有 #{tid}"}
    return {"ok": True, "id": t["id"], "notice": f"💬 工單 #{t['id']} 新留言（{who}）：{text[:150]}"}


def report_comment(who, code, text):
    text = str(text).strip()[:1000]
    st = _load()
    if not text or code not in st:
        return {"ok": False, "error": "留言是空的或異常碼不存在"}
    st[code].setdefault("留言", []).append({"時間": time.time(), "誰": who, "內容": text})
    _save(st)
    return {"ok": True, "code": code, "notice": f"💬 [{code}] 新留言（{who}）：{text[:150]}"}


def api(op, who, data):
    """中樞 /api/errors 的新 op 轉到這裡；回傳 dict（有 notice 的由中樞貼到互相討論群）。"""
    who = str(who or "使用者")[:30]
    if op == "ticket":
        return ticket_new(who, data.get("title") or "", data.get("desc") or "", data.get("pri") or "中",
                          data.get("to") or "自動派", data.get("level") or "")
    if op == "ticket_comment":
        return ticket_comment(who, data.get("id"), data.get("text") or "")
    if op == "report_comment":
        return report_comment(who, str(data.get("code")), data.get("text") or "")
    return {"ok": False, "error": "不認得的 op"}


def _ticket_section(esc):
    try:
        td = _td()
        d = td.load()
        err = ""
    except Exception as e:   # 待辦讀不到也別讓整頁掛掉
        td, d, err = None, [], f"（讀不到待辦：{esc(str(e))}）"
    open_ = sorted([t for t in d if t.get("狀態") != "完成"], key=lambda t: -t["id"])   # 工單 #144：全部給前端篩，顯示筆數用下拉
    PRI_N = {"高": 0, "中": 1, "低": 2}
    openers = sorted({str(t.get("開單人") or "未記") for t in open_})
    states = sorted({str(t.get("狀態") or "") for t in open_})
    rows = ""
    for t in open_:
        pri = str(t.get("優先") or "")
        rows += (f"<tbody class=tk data-id={t['id']} data-op=\"{esc(str(t.get('開單人') or '未記'))}\" data-st=\"{esc(str(t.get('狀態') or ''))}\" "
                 f"data-pri=\"{esc(pri)}\" data-pn={PRI_N.get(pri, 3)}>")
        last = esc(t["紀錄"][-1]) if t.get("紀錄") else ""
        hist = t.get("歷史") or []   # 網頁工單 #65：舊版留著，只顯示最新；點「舊版」才展開
        hist_html = ("<details><summary class=m>舊版 " + str(len(hist)) + " 筆</summary>"
                     + "".join(f"<div class=m>{esc(h.get('時間', ''))}｜{esc(h.get('欄位', ''))}｜舊：{esc(str(h.get('舊', ''))[:200])}</div>" for h in hist)
                     + "</details>") if hist else ""
        rows += (f"<tr><td>#{t['id']}</td><td>{esc(t.get('標題', ''))}"
                 + (f"<br><span class=m>└ {last}</span>" if last else "") + hist_html
                 + f"</td><td><b class=pri{PRI_N.get(pri, 3)}>{esc(pri or '—')}</b></td>"
                 f"<td>{esc(t.get('開單人') or '未記')}</td><td>{esc(t.get('負責', ''))}</td><td>{esc(t.get('狀態', ''))}</td>"
                 f"<td>{esc(td.level_text(t)) if td else ''}</td>"
                 f"<td><button onclick=\"tog('tc{t['id']}')\">留言</button></td></tr>"
                 f"<tr id=tc{t['id']} style=display:none><td></td><td colspan=7><input id=tci{t['id']} placeholder=\"留言給 #{t['id']}\"> "
                 f"<button onclick=\"tcom({t['id']})\">送出</button></td></tr></tbody>")
    rows = rows or "<tr><td colspan=8>沒有未完成工單</td></tr>"
    ck = lambda name, xs: "".join(f"<label class=ck><input type=checkbox class={name} value=\"{esc(x)}\" checked> {esc(x)}</label>" for x in xs)
    filt = (f"<div class=tf>顯示 <select id=fn>{''.join(('<option selected>' if n == 30 else '<option>') + str(n) + '</option>' for n in (20, 30, 40, 50))}</select> 筆"
            "｜排序 <select id=fs><option value=id_d>編號新→舊</option><option value=id_a>編號舊→新</option><option value=pri>優先 高→低</option>"
            "<option value=st>狀態</option><option value=op>開單人</option></select>"
            "｜優先 <select id=fp><option value=''>全部</option><option>高</option><option>中</option><option>低</option></select>"
            f"｜狀態 <select id=fst><option value=''>全部</option>{''.join(f'<option>{esc(s)}</option>' for s in states)}</select>"
            f"<details class=ckd><summary>開單人（{len(openers)}）</summary>{ck('fo', openers)}</details> <span id=fcnt class=m></span></div>")
    fjs = """<script>(()=>{const $=s=>document.querySelector(s);function run(){const n=+$('#fn').value,s=$('#fs').value,p=$('#fp').value,st=$('#fst').value;
const ops=new Set([...document.querySelectorAll('input.fo:checked')].map(x=>x.value));const tb=[...document.querySelectorAll('tbody.tk')];
const key={id_d:b=>-b.dataset.id,id_a:b=>+b.dataset.id,pri:b=>+b.dataset.pn*1e6-b.dataset.id,st:b=>b.dataset.st,op:b=>b.dataset.op}[s];
tb.sort((a,b)=>{const x=key(a),y=key(b);return x<y?-1:x>y?1:0;});const tbl=tb.length?tb[0].parentNode:null;let shown=0,match=0;
tb.forEach(b=>{if(tbl)tbl.appendChild(b);const ok=(!p||b.dataset.pri===p)&&(!st||b.dataset.st===st)&&ops.has(b.dataset.op);if(ok)match++;const v=ok&&shown<n;if(v)shown++;b.style.display=v?'':'none';});
$('#fcnt').textContent='符合 '+match+' 張，顯示 '+shown+' 張';try{localStorage.setItem('tk-f',JSON.stringify({n,s,p}));}catch(e){}}
try{const o=JSON.parse(localStorage.getItem('tk-f')||'{}');if(o.n)$('#fn').value=o.n;if(o.s)$('#fs').value=o.s;if(o.p!=null)$('#fp').value=o.p;}catch(e){}
['#fn','#fs','#fp','#fst'].forEach(i=>$(i).onchange=run);document.querySelectorAll('input.fo').forEach(x=>x.onchange=run);run();})();</script>"""
    opt = lambda xs, sel: "".join(f"<option{' selected' if x == sel else ''}>{x}</option>" for x in xs)
    return f"""<section><h3>📝 開工單</h3>
<input id=tt placeholder="標題（必填）"><br><br><textarea id=td rows=3 placeholder="說明（要做什麼、要交什麼、相關路徑）"></textarea><br><br>
優先 <select id=tp>{opt(['高', '中', '低'], '中')}</select>
指定給 <select id=tto>{opt(ASSIGNEES, '自動派')}</select>
難度 <select id=tl><option value="">讓 Claude 定</option>{opt(['小', '中', '大'], '')}</select>
<button onclick="tnew()">建立工單</button> <span id=tst class=m></span>
<p class=m>「自動派」＝負責人寫「未定」，派工建議下一輪會排。工單存在 H:\\Agent共用\\待辦\\待辦.json。</p>
<details open><summary><b>AI 開單格式（給 AI 看；網頁開的單會自動填）</b></summary><pre class=m style="white-space:pre-wrap">python H:\\Agent共用\\待辦\\待辦.py 加 "標題（來源：房間 MM-DD HH:MM 誰說的）" 負責人 高|中|低 --by 開單人 --概要 "誰提 → 誰補 → 誰決定"
  開單人：寫自己的名字（Claude／Codex／Gemini／輝達／DeepSeek／使用者）；不寫會被記成「未記」
  概要：1～3 行講這張單怎麼來的；沒寫的話程式會從來源時間附近的對話自動擬一版（標「自動擬，待補」）
  難度：python 待辦.py 難 #N 小|中|大　　領單：待辦.py 領 #N 名字　　退單：待辦.py 退 #N 缺檔|權限不足|規格未定|超出能力|其他 "原因" 名字
  舊版：改標題／負責／優先／難度會自動留舊值，python 待辦.py 歷史 #N 查</pre></details></section>
<style>.tf{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0 8px}} .tf select{{padding:2px 4px}} .ckd{{display:inline-block}} .ckd .ck{{display:inline-block;margin-right:8px}}
.pri0{{color:#fff;background:#e53935;border-radius:6px;padding:0 6px}} .pri1{{color:#fff;background:#fb8c00;border-radius:6px;padding:0 6px}} .pri2{{color:#fff;background:#78909c;border-radius:6px;padding:0 6px}}</style>
<section><h3>📋 未完成工單（{len(open_)}）{err}</h3>{filt}
<table><tr><th>#</th><th>標題</th><th>優先</th><th>開單人</th><th>負責</th><th>狀態</th><th>難度</th><th></th></tr>{rows}</table></section>{fjs}"""


def page(key=""):
    """網頁「工單＋報錯」頁（使用者 11-21 01:48：「報錯也弄個顯示，大家看得到就會認領分派了」；11-21 22:37 加工單＋留言）。"""
    import html as _h
    esc = _h.escape
    st = _load()
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "—"
    act = sorted([(c, s) for c, s in st.items() if s.get("狀態") == "異常中"], key=lambda x: x[1].get("出現", 0))
    done = sorted([(c, s) for c, s in st.items() if s.get("狀態") == "已恢復"], key=lambda x: -x[1].get("恢復", 0))[:15]
    cid = lambda c: "rc" + re.sub(r"\W", "_", c)
    coms = lambda s: "".join(f"<br><span class=m>💬 {fmt(m.get('時間'))} {esc(str(m.get('誰', '')))}：{esc(str(m.get('內容', '')))}</span>"
                             for m in s.get("留言", []))
    rows = "".join(
        f"<tr><td><code>{esc(c)}</code></td><td>{esc(s.get('說明', ''))}{coms(s)}</td><td>{esc(s.get('負責', ''))}</td>"
        f"<td>{esc(s.get('認領') or '')}</td><td>{fmt(s.get('出現'))}<br><span class=m>{s.get('次數', 1)} 次</span></td>"
        f"<td><button onclick=\"act('claim','{esc(c)}')\">我來</button> <button onclick=\"act('resolve','{esc(c)}')\">已修</button> "
        f"<button onclick=\"tog('{cid(c)}')\">留言</button></td></tr>"
        f"<tr id={cid(c)} style=display:none><td></td><td colspan=5><input id={cid(c)}i placeholder=\"留言給 {esc(c)}\"> "
        f"<button onclick=\"rcom('{esc(c)}','{cid(c)}i')\">送出</button></td></tr>"
        for c, s in act) or "<tr><td colspan=6>目前沒有異常 🎉</td></tr>"
    drows = "".join(f"<tr><td><code>{esc(c)}</code></td><td>{esc(s.get('說明', '')[:120])}{coms(s)}</td><td>{fmt(s.get('出現'))} → {fmt(s.get('恢復'))}</td>"
                    f"<td>{esc(s.get('修好者') or '自動')}</td><td>{esc(s.get('修法') or '')}</td></tr>" for c, s in done) or "<tr><td colspan=5>—</td></tr>"
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工單＋報錯</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--acc:#2e6fd9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35;--acc:#7fa8f0}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:960px;margin:0 auto;padding:12px 16px;overflow-x:hidden}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:10px 0;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td,th{{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
.m{{color:var(--mute);font-size:12px}} textarea,input{{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}}
td input{{width:calc(100% - 70px)}} select{{padding:4px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);margin-right:8px}}
button{{padding:4px 10px}}
</style></head><body>
<p class=m>你的名字（工單、留言、報錯共用）：<input id=who value="使用者" style="width:160px"></p>
{_ticket_section(esc)}
<section><h3>🩺 異常中（{len(act)}）</h3><p class=m>自動偵測的修好後會自動消失；人工回報的要按「已修」。按「我來」＝認領。</p>
<table><tr><th>異常碼</th><th>說明</th><th>負責</th><th>認領</th><th>出現</th><th></th></tr>{rows}</table></section>
<section><h3>🐞 發現哪裡怪？（報錯）</h3>
<textarea id=txt rows=3 placeholder="哪裡怪、怎麼重現（越具體越好）"></textarea><br><button onclick="rep()">提交到報錯系統</button> <span id=st class=m></span></section>
<section><h3>✅ 最近恢復</h3><table><tr><th>異常碼</th><th>說明</th><th>時間</th><th>誰修的</th><th>怎麼修</th></tr>{drows}</table></section>
<p class=m>給 AI 讀的版本：H:\\Agent共用\\系統異常.md（中樞每 10 分鐘更新）</p>
<script>const K={json.dumps(key)};
async function post(b){{const r=await fetch('/api/errors',{{method:'POST',headers:{{'X-Key':K,'Content-Type':'application/json'}},body:JSON.stringify(b)}});return r.json();}}
async function rep(){{const t=document.getElementById('txt').value.trim();if(!t)return;document.getElementById('st').textContent='送出中…';
const j=await post({{op:'report',who:document.getElementById('who').value||'使用者',text:t}});document.getElementById('st').textContent=j.ok?('已建立 '+j.code):'失敗';setTimeout(()=>location.reload(),800);}}
async function act(op,code){{const who=document.getElementById('who').value||'使用者';let note='';if(op==='resolve'){{note=prompt('怎麼修的？（可留空）')||'';}}
const j=await post({{op,code,who,note}});if(j.ok)location.reload();else alert('失敗');}}
const W=()=>document.getElementById('who').value||'使用者', V=id=>document.getElementById(id).value;
function tog(id){{const e=document.getElementById(id);e.style.display=e.style.display==='none'?'':'none';}}
async function tnew(){{const t=V('tt').trim(),s=document.getElementById('tst');if(!t){{s.textContent='標題必填';return;}}s.textContent='建立中…';
const j=await post({{op:'ticket',who:W(),title:t,desc:V('td'),pri:V('tp'),to:V('tto'),level:V('tl')}});
s.textContent=j.ok?('已建立 #'+j.id):('失敗：'+(j.error||''));if(j.ok)setTimeout(()=>location.reload(),1200);}}
async function tcom(id){{const t=V('tci'+id).trim();if(!t)return;const j=await post({{op:'ticket_comment',who:W(),id,text:t}});if(j.ok)location.reload();else alert('失敗：'+(j.error||''));}}
async function rcom(code,inp){{const t=V(inp).trim();if(!t)return;const j=await post({{op:'report_comment',who:W(),code,text:t}});if(j.ok)location.reload();else alert('失敗：'+(j.error||''));}}
</script></body></html>"""


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[:1] == ["回報"] and len(a) >= 3:
        print("已建立", report(a[1], " ".join(a[2:]))); sys.exit()
    if a[:1] == ["認領"] and len(a) >= 3:
        print("認領", "成功" if claim(a[1], a[2]) else "失敗（碼不存在或已恢復）"); sys.exit()
    if a[:1] == ["已修"] and len(a) >= 3:
        print("標已修", "成功" if resolve(a[1], a[2], " ".join(a[3:])) else "失敗"); sys.exit()
    print(check() or "（沒有新變化）")
    print(open(OUT, encoding="utf-8").read())
