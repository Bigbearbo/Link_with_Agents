r"""派報生：每位 AI 訂閱自己想追的消息，並自己設定「什麼消息一來就提前叫醒我」。另有總指揮巡視報告。
使用者 11-20 00:22：「AI 總指揮先給 Claude，定期負責觀察大家的用量和工作內容。建立派報生系統，訂閱自己想追蹤的消息，自己設定收報提前喚醒規則。」

訂閱檔（各自改，中樞每 2 分鐘讀一次）：
  輝達 H:\免費模型用\訂閱.json、Gemini H:\Gemini用\訂閱.json、DeepSeek H:\DeepSeek用\訂閱.json、
  Claude／Codex H:\Agent共用\訂閱\<名字>.json
  原本的「關鍵字」照舊是 30 分鐘摘要；新增「喚醒」＝提前叫醒規則：
  "喚醒": {"關鍵字": ["手搓", "@輝達"], "來源": ["變動", "群組"], "冷卻分鐘": 30}
  - 變動＝共用區檔案變動（路徑／負責人／摘要含關鍵字）；群組＝機器人互相討論群的新發言（主討論是私人對話，不掃）
  - 自己造成的變動、自己的發言不算；同一位冷卻時間內只叫一次
  python 派報生.py 巡視      總指揮巡視報告（印出並寫 H:\Agent共用\總指揮日誌.md）
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = r"H:\Agent共用"
SUBS = {"輝達": r"H:\免費模型用\訂閱.json", "Gemini": r"H:\Gemini用\訂閱.json", "DeepSeek": r"H:\DeepSeek用\訂閱.json",
        "Claude": os.path.join(SHARED, "訂閱", "Claude.json"), "Codex": os.path.join(SHARED, "訂閱", "Codex.json")}
CHANGE_LOG = os.path.join(HERE, "logs", "共用區變動.jsonl")
WAKE_LOG = os.path.join(HERE, "logs", "派報生.jsonl")
DEFAULT_WAKE = {"Claude": ["@Claude", "中樞", "三方討論", "權限"], "Codex": ["@Codex", "Codex工作清單", "驗收"],
                "Gemini": ["@Gemini"], "輝達": ["@輝達"], "DeepSeek": ["@DeepSeek", "小模型池"]}


def load_rule(name):
    p = SUBS[name]
    try:
        r = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        r = {}
    if "喚醒" not in r:   # 第一次：補一份預設，讓它自己改
        r.setdefault("_說明", f"{name} 的訂閱。關鍵字＝30 分鐘摘要；喚醒＝符合就提前叫醒你（派報生）。自己改。")
        r.setdefault("關鍵字", [name])
        r["喚醒"] = {"關鍵字": DEFAULT_WAKE.get(name, [name]), "來源": ["變動", "群組"], "冷卻分鐘": 30}
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(r, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except OSError:
            pass
    return r.get("喚醒") or {}


def new_changes(since):
    out = []
    try:
        for line in open(CHANGE_LOG, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("時間", "") > since:
                out.append(r)
    except OSError:
        pass
    return out


def matches(name, wake, changes, msgs):
    """回傳第一個符合的 (關鍵字, 說明)；沒有回 None。"""
    kws = [k for k in wake.get("關鍵字", []) if k]
    src = wake.get("來源") or ["變動", "群組"]
    if "變動" in src:
        for r in changes:
            if name and name in (r.get("負責") or ""):
                continue   # 自己改的不叫自己
            hay = f"{r.get('路徑', '')} {r.get('負責', '')} {r.get('摘要', '')}"
            for k in kws:
                if k.lower() in hay.lower():
                    return k, f"共用區{r.get('類', '')}：{r.get('路徑', '')}" + (f"（{r['摘要'][:40]}）" if r.get("摘要") else "")
    if "群組" in src:
        for who, t, text in msgs:
            if who == name:
                continue
            for k in kws:
                if k.lower() in (text or "").lower():
                    return k, f"{who} {t}：{(text or '').strip().splitlines()[0][:60]}"
    return None


def wake_loop(hub, log):
    """中樞背景執行：每 2 分鐘掃一次，符合就叫醒（視窗 agent 用叫醒通道，其他用群組點名）。"""
    import window_agent as wa
    last_time = time.strftime("%Y-%m-%d %H:%M")
    room = hub.rooms.get("g1004308131170")
    last_idx = len(room.transcript) if room else 0
    cooldown = {}
    while True:
        time.sleep(120)
        try:
            room = hub.rooms.get("g1004308131170")
            changes = new_changes(last_time)
            last_time = time.strftime("%Y-%m-%d %H:%M")
            msgs = []
            if room:
                for m in room.transcript[last_idx:]:
                    if m.get("who") != "系統":
                        msgs.append((m.get("who"), m.get("time", ""), m.get("text", "")))
                last_idx = len(room.transcript)
            if not changes and not msgs:
                continue
            for name in SUBS:
                wake = load_rule(name)
                if not wake or time.time() - cooldown.get(name, 0) < 60 * float(wake.get("冷卻分鐘", 30)):
                    continue
                hit = matches(name, wake, changes, msgs)
                if not hit:
                    continue
                cooldown[name] = time.time()
                kw, why = hit
                text = f"【派報生】你訂的「{kw}」有新消息：{why}。要不要處理你自己決定；規則在 {SUBS[name]}。"
                if name in wa.HOMES:
                    r = wa.nudge(name, "g1004308131170", custom=text)
                elif room and name in ("DeepSeek", "Codex", "Claude"):
                    if any(f"@{name}" in m[2] for m in msgs):
                        r = "群組已點名，不重複叫"
                    else:
                        room.notice(f"@{name} " + text)
                        room.stop_flag = False
                        room.chain([{"DeepSeek": room.deepseek, "Codex": room.codex, "Claude": room.claude}[name]])
                        r = "群組點名"
                else:
                    continue
                with open(WAKE_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"時間": time.strftime("%m-%d %H:%M"), "給": name, "關鍵字": kw, "原因": why, "結果": r},
                                       ensure_ascii=False) + "\n")
                log(f"派報生：{name}「{kw}」→ {r}")
        except Exception as e:
            log(f"派報生錯誤：{type(e).__name__} {e}")


def patrol():
    """總指揮巡視：用量、各位最近一次發言、待回單、派報生紀錄。寫 H:\\Agent共用\\總指揮日誌.md（只留最新一份）。"""
    import sys
    sys.path.insert(0, HERE)
    lines = [f"# 總指揮巡視（Claude）｜{time.strftime('%Y-%m-%d %H:%M')}",
             "摘要：Claude 每小時看一次大家的用量和工作；有異常才在群組講。使用者 11-20 00:22 指派。", ""]
    try:
        state = json.load(open(os.path.join(HERE, "tg_state.json"), encoding="utf-8"))
        lastsay = {}
        for rid, r in state.get("rooms", {}).items():
            for m in r.get("transcript", [])[-300:]:
                lastsay[m.get("who")] = max(lastsay.get(m.get("who"), ""), m.get("time", ""))
        lines.append("## 最近一次發言")
        for who in ("Claude", "Codex", "Gemini", "輝達", "DeepSeek", "Codex實驗主持"):
            lines.append(f"- {who}：{lastsay.get(who, '（沒有）')}")
    except (OSError, ValueError) as e:
        lines.append(f"（讀房間狀態失敗：{type(e).__name__}）")
    lines.append("\n## 還沒處理的待回單")
    for name, home in (("輝達", r"H:\免費模型用"), ("Gemini", r"H:\Gemini用")):
        box = os.path.join(home, "群組", "待回")
        try:
            items = [f for f in os.listdir(box) if f.endswith(".md")]
        except OSError:
            items = []
        old = [f for f in items if time.time() - os.path.getmtime(os.path.join(box, f)) > 1800]
        lines.append(f"- {name}：{len(items)} 張" + (f"，其中 {len(old)} 張超過 30 分鐘沒處理：{'、'.join(old)}" if old else ""))
    lines.append("\n## 用量核對")
    try:
        import importlib.util as u
        s = u.spec_from_file_location("hubm", os.path.join(HERE, "hub.py"))
        m = u.module_from_spec(s)
        sys.argv = ["x"]
        s.loader.exec_module(m)
        import 用量核對 as uc
        lines.append(uc.report(uc.check(m.usage_summary())))
    except Exception as e:
        lines.append(f"（核對失敗：{type(e).__name__} {e}）")
    lines.append("\n## 成果指標（使用者 11-20 01:54：看 AI 的效率、產出、修復、發展空間，比數 PR 有意義）")
    try:
        import re as _re, statistics as _st
        from datetime import datetime as _D
        todo = json.load(open(os.path.join(SHARED, "待辦", "待辦.json"), encoding="utf-8"))
        yr = time.strftime("%Y-")
        hrs, inter, solo = [], [], []
        for t in todo:
            recs = [str(r.get("內容", r) if isinstance(r, dict) else r) for r in (t.get("紀錄") or [])]
            inter.append(sum("使用者" in x for x in recs))
            done = [x for x in recs if _re.match(r"\d\d-\d\d \d\d:\d\d 完", x)]
            if t.get("狀態") == "完成" and done and t.get("建立"):
                solo.append(inter[-1] == 0)
                hrs.append((_D.strptime(yr + done[-1][:11], "%Y-%m-%d %H:%M") - _D.strptime(yr + t["建立"][:11], "%Y-%m-%d %H:%M")).total_seconds() / 3600)
        lines.append(f"- 待辦 {len(todo)} 項、完成 {len(hrs)}；建立→完成中位數 {(_st.median(hrs) if hrs else 0):.1f} 小時；平均每項使用者介入 {(sum(inter) / max(1, len(inter))):.1f} 次；完成的裡 AI 自己跑完 {sum(solo)} 項、你出過手 {len(solo) - sum(solo)} 項")
    except Exception as e:
        lines.append(f"- 算不出來（{type(e).__name__}）")
    lines.append("\n## 註解層指標（研究想法 018：有註解後還會不會再誤會、註解會不會越補越亂）")
    def sections(fn):
        try:
            return [x for x in open(os.path.join(SHARED, fn), encoding="utf-8").read().split("\n## ")[1:] if "｜" in x.split("\n", 1)[0]]
        except OSError:
            return []
    try:
        pend = open(os.path.join(SHARED, "待補註解.md"), encoding="utf-8").read()
        p_open, p_done = pend.count("- [ ]"), pend.count("- [x]")
    except OSError:
        p_open = p_done = 0
    lines.append(f"- 註解 {len(sections('註解.md'))} 條；誤會更正 {len(sections('誤會更正.md'))} 條；待補註解 開著 {p_open}／已補 {p_done}")
    lines.append("\n## 研究頁新留言（使用者 11-20 00:44：留言累積，AI 巡視時看、更新那一項；處理完在那行尾加「（已處理）」）")
    import glob
    found = []
    for f in glob.glob(os.path.join(SHARED, "研究想法庫", "[0-9][0-9][0-9]-*.md")):
        for ln in open(f, encoding="utf-8").read().splitlines():
            if "【使用者留言" in ln and "（已處理" not in ln:
                found.append(f"- {os.path.basename(f)[:3]}：{ln.strip()[:120]}")
    try:
        for t in json.load(open(os.path.join(SHARED, "待辦", "待辦.json"), encoding="utf-8")):
            recs = [(r.get("內容", "") if isinstance(r, dict) else str(r)) for r in (t.get("紀錄") or [])]
            # 待辦的留言：後面有任何一筆新紀錄（AI 回應過）就算處理了
            if recs and "使用者留言" in recs[-1] and t.get("狀態") != "完成":
                found.append(f"- 待辦 #{t['id']}：{recs[-1][:120]}")
    except (OSError, ValueError):
        pass
    lines += found or ["（沒有）"]
    lines.append("\n## 派報生最近叫醒")
    try:
        recent = open(WAKE_LOG, encoding="utf-8").read().splitlines()[-8:]
        lines += [f"- {json.loads(x)['時間']} {json.loads(x)['給']}「{json.loads(x)['關鍵字']}」{json.loads(x)['結果']}" for x in recent] or ["（還沒有）"]
    except OSError:
        lines.append("（還沒有）")
    text = "\n".join(lines) + "\n"
    open(os.path.join(SHARED, "總指揮日誌.md"), "w", encoding="utf-8").write(text)
    return text


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["巡視"]:
        print(patrol())
    else:
        for n in SUBS:
            print(n, load_rule(n))
