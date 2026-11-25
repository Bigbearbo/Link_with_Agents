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
import json, os, re, sys, time


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
SHARED = r"H:\Agent共用"
SUBS = {"輝達": r"H:\免費模型用\訂閱.json", "Gemini": r"H:\Gemini用\訂閱.json", "DeepSeek": r"H:\DeepSeek用\訂閱.json",
        "Claude": os.path.join(SHARED, "訂閱", "Claude.json"), "Codex": os.path.join(SHARED, "訂閱", "Codex.json")}
CHANGE_LOG = os.path.join(HERE, "logs", "共用區變動.jsonl")
WAKE_LOG = os.path.join(HERE, "logs", "派報生.jsonl")
DEFAULT_WAKE = {"Claude": ["@Claude", "中樞", "三方討論", "權限"], "Codex": ["@Codex", "Codex工作清單", "驗收"],
                "Gemini": ["@Gemini"], "輝達": ["@輝達"], "DeepSeek": ["@DeepSeek", "小模型池"]}


INBOX_ONLY = {"電腦Codex"}   # 沒有視窗控制介面、不在中樞輪流裡：叫醒＝寫收件檔＋群組 @（使用者 11-24 04:0x）
INBOX_DIR = os.path.join(SHARED, "訂閱", "收件")


def inbox_drop(name, text, why):
    """寫 H:\\Agent共用\\訂閱\\收件\\<名字>.md（新的在上面，留最近 10 筆）；讀完可刪或清空。回檔案路徑。"""
    os.makedirs(INBOX_DIR, exist_ok=True)
    p = os.path.join(INBOX_DIR, f"{name}.md")
    try:
        old = open(p, encoding="utf-8").read().split("\n## ")[1:10]
    except OSError:
        old = []
    head = f"# {name} 收件（派報生寫；讀完可刪或清空這個檔）\n"
    body = f"\n## {time.strftime('%m-%d %H:%M')}｜{why}\n{text}\n" + "".join("\n## " + x for x in old)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        f.write(head + body)
    os.replace(p + ".tmp", p)
    return p


def ticket_drop(name, text, why):
    """視窗 agent 叫不醒時，在它家 群組\\待回\\ 留一張單（等單.py 會醒）。累積喚醒單只留最新一張。回結果文字。"""
    import window_agent as wa
    box = os.path.join(wa.HOMES[name], "群組", "待回")
    os.makedirs(box, exist_ok=True)
    p = os.path.join(box, f"派報-{why}.md")
    mode = "w" if why == "累積喚醒" else "a"
    with open(p, mode, encoding="utf-8") as f:
        f.write(f"# 派報：{why}｜{time.strftime('%m-%d %H:%M')}\n{text}\n\n讀完把這張單移到 待回\\已處理\\（只需讀、有要回的再回）。\n")
    return f"留單 {p}"


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


def all_hits(name, wake, changes, msgs, limit=6):
    """所有符合的消息（給小 AI 編新聞用），最多 limit 則。"""
    out, seen = [], set()
    kws = [k for k in wake.get("關鍵字", []) if k]
    src = wake.get("來源") or ["變動", "群組"]
    if "變動" in src:
        for r in changes:
            if name and name in (r.get("負責") or ""):
                continue
            hay = f"{r.get('路徑', '')} {r.get('負責', '')} {r.get('摘要', '')}"
            if any(k.lower() in hay.lower() for k in kws) and hay not in seen:
                seen.add(hay)
                out.append(f"共用區{r.get('類', '')}｜{r.get('路徑', '')}｜{r.get('負責', '')}｜{(r.get('摘要') or '')[:120]}")
    if "群組" in src:
        for who, t, text in msgs:
            if who != name and any(k.lower() in (text or "").lower() for k in kws):
                out.append(f"機器人群 {who} {t}｜{(text or '').strip()[:300]}")
    return out[:limit]


def news_router(name, wake, hits):
    """使用者 11-20 02:47／02:48：「小 AI 當報紙新聞員，幫忙整理其他 AI 感興趣的資訊，再依條件發送；
    報錯其實就是一種即時派報，小 AI 先檢查決定要不要叫醒 AI」「就是程式喚醒再加一層小 AI 當路由」。
    程式關鍵字先篩出候選 → 小 AI 編成新聞（發生了什麼／為什麼跟你有關／原文在哪）並決定：即時叫醒 or 放進下一期摘要。
    只送共用區變動摘要和機器人群內容（不送主討論私聊）；用免費 groq。小 AI 失敗就退回原本的程式叫醒。
    回 (叫醒?, 新聞文字)。"""
    # 使用者 02:48「每次再依照狀況回修程式，跟 AI 流水生產線是同一個意思」：
    # 小 AI 判錯的情況，確認穩定後寫成固定規則（派報規則.json），程式先查規則，命中就不問小 AI。
    # 規則格式：[{"給": "Codex 或 *", "含": ["訓練", "完成收據"], "叫醒": false, "來源": "誤判 11-20 …"}]
    try:
        rules = json.load(open(os.path.join(HERE, "派報規則.json"), encoding="utf-8"))
    except (OSError, ValueError):
        rules = []
    blob = "\n".join(hits)
    for r in rules:
        if r.get("給", "*") in ("*", name) and all(k in blob for k in r.get("含", [])):
            return bool(r.get("叫醒")), f"（固定規則：{r.get('來源', '')}）\n" + "\n".join(hits[:3])
    try:
        import importlib
        sys.path.insert(0, r"H:\Agent共用\ai-call")
        pool = importlib.import_module("免費AI池")
        interest = wake.get("興趣") or "、".join(wake.get("關鍵字", []))
        prompt = (f"你是派報新聞員，替 AI 同事「{name}」整理消息。{name} 關心：{interest}。\n"
                  f"即時叫醒的條件：{wake.get('叫醒條件') or '需要他現在動手（他負責的東西壞了、有人在等他、點名問他），只是資訊更新就放下一期摘要'}。\n"
                  "候選消息：\n" + "\n".join("- " + h for h in hits) +
                  # 11-25 三次把別人的工作寫成「Claude 宣布 N 分鐘完成」「這屬於 Codex 負責」→ 明令不准推測歸屬、時程
                  "\n嚴格規則：只寫候選消息裡明寫的事；誰說的、誰負責、多久完成，原文沒寫就不要寫，不准推測或補上時間。"
                  "負責人以原文寫的為準，原文寫「請 X 驗收」就照寫，不要改成別人。\n"
                  # 11-25 11:44 第 4 次誤植（「此項工作屬於 Codex 的…範疇」）→ 改成逐字摘錄，不准自己判斷歸屬
                  "不准寫「屬於某某負責／範疇／工作」這類歸屬判斷；新聞第一行必須是「誰 幾點：」加上原文逐字摘錄（最多 80 字），第二行才寫為什麼可能跟他有關。\n"
                  '\n只回 JSON：{"叫醒": true 或 false, "新聞": "2～3 行繁體中文：第一行「誰 幾點：」加原文逐字摘錄；第二行為什麼可能跟他有關；第三行原文在哪"}')
        raw = pool.ask("groq", prompt, max_tokens=1200, timeout=40)
        m = re.search(r"\{.*\}", raw or "", re.S)
        j = json.loads(m.group(0))
        news = str(j.get("新聞") or "").strip()
        # 11-25 16:33 第 5 次誤植：小 AI 不照「逐字摘錄」格式 → 程式檢查：新聞裡要有一段 ≥12 字是候選原文逐字；
        # 沒有就丟掉它的轉述，改用程式直接摘錄候選原文（不再讓小 AI 改寫誰說了什麼）
        flat = re.sub(r"\s+", "", "".join(hits))
        body = re.sub(r"\s+", "", news)
        # 16:35 Codex：摘對 12 字仍可能把「給別人的建議」報成「你需立即處理」→ 小 AI 只負責判斷叫不叫醒，
        # 新聞內容一律由程式直接摘錄原文（誰、幾點、原句），不再附小 AI 的「為什麼跟你有關」
        _ = body, flat
        news = "原文摘錄：\n" + "\n".join("・" + h[:200] for h in hits[:3])
        return bool(j.get("叫醒")), news
    except Exception:
        return True, ""


def wake_loop(hub, log):
    """中樞背景執行：每 2 分鐘掃一次，符合就叫醒（視窗 agent 用叫醒通道，其他用群組點名）。"""
    import window_agent as wa
    last_time = time.strftime("%Y-%m-%d %H:%M")
    room = hub.rooms.get("g1004308131170")
    last_idx = len(room.transcript) if room else 0
    cooldown = {}
    try:   # 開機時已有的請求不補送；之後（含第一輪累積喚醒寫的）都要送
        wake_loop.req_seen = len(open(os.path.join(HERE, "logs", "派報", "叫醒請求.jsonl"), encoding="utf-8").read().splitlines())
    except OSError:
        wake_loop.req_seen = 0
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
            # 提醒系統的急件（工單 #267：提醒.py 依收件人推送規則判「急」→ 寫 叫醒請求.jsonl）：照冷卻叫醒，不經小 AI
            # 累積喚醒（使用者 11-24 04:0x）：主討論累積則數到門檻 → 累積喚醒.py 寫一筆叫醒請求，下面照同一管道送
            try:
                import 累積喚醒
                累積喚醒.run(log=log)
            except Exception as e:
                log(f"累積喚醒錯誤：{type(e).__name__} {e}")
            reqp = os.path.join(HERE, "logs", "派報", "叫醒請求.jsonl")
            try:
                lines = open(reqp, encoding="utf-8").read().splitlines()
            except OSError:
                lines = []
            todo = getattr(wake_loop, "retry", [])   # 上一輪撞到冷卻、先擱著的累積喚醒
            wake_loop.retry = []
            for ln in lines[wake_loop.req_seen if hasattr(wake_loop, "req_seen") else len(lines):]:
                try:
                    todo.append(json.loads(ln))
                except ValueError:
                    continue
            for rq in todo:
                name = str(rq.get("給") or "")
                acc = rq.get("原因") == "累積喚醒"
                if name not in SUBS and name not in wa.HOMES and name not in INBOX_ONLY:
                    continue
                if time.time() - cooldown.get(name, 0) < 60 * _P("急件冷卻分", 10):   # 急件冷卻 10 分（見團隊參數：急件冷卻分）
                    if acc and time.time() - rq.setdefault("_擱", time.time()) < 7200:
                        wake_loop.retry.append(rq)   # 累積喚醒不丟：冷卻過了再送（最多擱 2 小時）
                    continue
                cooldown[name] = time.time()
                text = (f"{rq.get('內容') or ''}" if acc else
                        f"【提醒】{rq.get('內容') or ''}（{rq.get('原因') or '提醒系統急件'}；規則在 H:\\Agent共用\\訂閱\\{name}.json 的推送規則）")
                if name in INBOX_ONLY:   # 沒有視窗控制介面（電腦Codex）：寫收件檔＋群組 @，不接力
                    r = inbox_drop(name, text, rq.get("原因") or "提醒")
                    if room:
                        room.notice(f"@{name} {text.splitlines()[0][:200]}（全文在 {r}）")
                    r = f"收件檔＋群組 @：{r}"
                elif name in wa.HOMES:
                    r = wa.nudge(name, "g1004308131170", custom=text)
                    if "只留單" in r:   # agy（Gemini）沒有輸入框介面：留一張單，它的 等單.py 會醒
                        r = ticket_drop(name, text, rq.get("原因") or "提醒") + f"（{r}）"
                elif room and name in ("DeepSeek", "Codex", "Claude"):
                    room.notice(f"@{name} " + text)
                    room.stop_flag = False
                    room.chain([{"DeepSeek": room.deepseek, "Codex": room.codex, "Claude": room.claude}[name]])
                    r = "群組點名"
                else:
                    continue
                with open(WAKE_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"時間": time.strftime("%m-%d %H:%M"), "給": name, "關鍵字": "累積喚醒" if acc else "提醒急件",
                                        "原因": rq.get("原因"), "結果": r}, ensure_ascii=False) + "\n")
                log(f"派報生：{'累積喚醒' if acc else '提醒急件'} {name} → {r}")
            wake_loop.req_seen = len(lines)
            if not changes and not msgs:
                continue
            for name in SUBS:
                wake = load_rule(name)
                if not wake or time.time() - cooldown.get(name, 0) < 60 * float(wake.get("冷卻分鐘", 30)):
                    continue
                hit = matches(name, wake, changes, msgs)
                if not hit:
                    continue
                kw, why = hit
                hits = all_hits(name, wake, changes, msgs)
                urgent, news = news_router(name, wake, hits)
                # 每次路由決定都記下來（使用者 02:51：小 AI 判對的也要看能不能寫成規則，讓程式逐漸取代小 AI）
                os.makedirs(os.path.join(HERE, "logs", "派報"), exist_ok=True)
                with open(os.path.join(HERE, "logs", "派報", "路由紀錄.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"時間": time.strftime("%Y-%m-%d %H:%M"), "給": name, "關鍵字": kw, "候選": hits,
                                        "叫醒": urgent, "誰決定": "規則" if news.startswith("（固定規則") else "小AI",
                                        "判對": None}, ensure_ascii=False) + "\n")
                if not urgent:   # 小 AI 判斷不急 → 放進下一期摘要，不叫醒
                    os.makedirs(os.path.join(HERE, "logs", "派報"), exist_ok=True)
                    with open(os.path.join(HERE, "logs", "派報", f"{name}-下一期.md"), "a", encoding="utf-8") as f:
                        f.write(f"\n## {time.strftime('%m-%d %H:%M')}（訂閱「{kw}」）\n{news or why}\n")
                    with open(WAKE_LOG, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"時間": time.strftime("%m-%d %H:%M"), "給": name, "關鍵字": kw, "原因": why,
                                            "結果": "小 AI 判斷不急，放下一期摘要"}, ensure_ascii=False) + "\n")
                    continue
                cooldown[name] = time.time()
                text = (f"【派報生】{news}\n（你訂的「{kw}」；小 AI 判斷要現在叫你。規則在 {SUBS[name]}）" if news else
                        f"【派報生】你訂的「{kw}」有新消息：{why}。要不要處理你自己決定；規則在 {SUBS[name]}。")
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
