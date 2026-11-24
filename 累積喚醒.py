r"""累積喚醒：主討論累積多少則新訊息，就推一則叫醒請你讀紀錄、跟上進度。
使用者 11-24 04:0x：「新增訂閱機制，主討論累積多少則訊息就推送喚醒讀紀錄，稍微跟上進度，以免後來沒讀，可以變成你設計推送規則。」

規則（Claude 設計）：
  訂閱檔 H:\Agent共用\訂閱\<名字>.json 的 "累積" 欄位：
    "累積": {"房間": "主討論", "則數": 20, "冷卻分鐘": 60, "只算": ["使用者", "旁聽", "AI"], "排除含": ["資源推播"], "發言名": ["Gemini"]}
    - 則數／冷卻分鐘沒寫 → 團隊參數「累積喚醒則數」「累積喚醒冷卻分」
    - 只算：使用者（使用者本人發言）、旁聽（視窗同步寫進來的）、AI（其他 AI 的回覆）、系統（中樞通知；預設不算）
    - 排除含：內文含這些字的不算（共用區變動、資源推播之類的通知）
    - 排除標題含：發言標題含這些字的不算（Claude 用來排除自己視窗的旁聽，例「在 終端機 Claude 視窗」）
    - 發言名：紀錄裡哪些發言者算「你本人」（預設＝名字）；你自己的發言不算則數
  「讀到哪」：你在該房間發言、或被累積喚醒的那一刻，就算讀到那裡。之後新增的（只算的那幾類）達到則數、
  且距上次累積喚醒超過冷卻 → 寫一筆 叫醒請求.jsonl（原因＝累積喚醒），由派報生照既有管道送出。
  內文附小 AI 3～5 行摘要（免費AI池 groq → nvidia，送前過 隱私篩；都失敗就列發言者＋開頭 30 字）。
  記帳：logs\派報\累積喚醒記帳.jsonl（誰、則數、門檻、摘要模型）；統計 `python 累積喚醒.py 統計`。

  python 累積喚醒.py 乾跑        每位訂閱者目前累積幾則、會不會觸發（不寫任何檔）
  python 累積喚醒.py 跑          照規則掃一次（中樞派報生每 2 分鐘自動呼叫）
  python 累積喚醒.py 統計        記帳統計，拿來調門檻
"""
import glob, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = r"H:\Agent共用"
SUB_DIR = os.path.join(SHARED, "訂閱")
LOGS_DAY = os.path.join(HERE, "logs-按日")
EXPORT = os.path.join(SHARED, "對話紀錄匯出")
PB = os.path.join(HERE, "logs", "派報")
STATE = os.path.join(PB, "累積喚醒狀態.json")
LEDGER = os.path.join(PB, "累積喚醒記帳.jsonl")
REQ = os.path.join(PB, "叫醒請求.jsonl")
DAYS = 3   # 最多往回看幾天的紀錄
HEAD = re.compile(r"^## (.+?)(\d\d-\d\d \d\d:\d\d)\s*$")   # 標籤保留原本的空白，f"## {標籤}{時間}" 就是原標題


def _P(名, 預設):
    try:
        import importlib.util as _u
        _m = sys.modules.get("團隊參數")
        if _m is None:
            _s = _u.spec_from_file_location("團隊參數", os.path.join(SHARED, "專案", "團隊系統", "團隊參數.py"))
            _m = _u.module_from_spec(_s)
            _s.loader.exec_module(_m)
            sys.modules["團隊參數"] = _m
        return _m.get(名, 預設)
    except Exception:
        return 預設


def rules():
    """{名字: 累積規則}；只收有 "累積" 欄位的訂閱檔。"""
    out = {}
    for p in sorted(glob.glob(os.path.join(SUB_DIR, "*.json"))):
        try:
            r = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        acc = r.get("累積")
        if isinstance(acc, dict) and acc.get("開", True):
            name = os.path.basename(p)[:-5]
            acc = dict(acc)
            acc.setdefault("房間", "主討論")
            acc["則數"] = int(acc.get("則數") or _P("累積喚醒則數", 20))
            acc["冷卻分鐘"] = float(acc.get("冷卻分鐘") or _P("累積喚醒冷卻分", 60))
            acc.setdefault("只算", ["使用者", "旁聽", "AI"])
            acc.setdefault("排除含", ["資源推播", "共用區變動"])
            acc.setdefault("發言名", [name])
            acc["_檔"] = p
            out[name] = acc
    return out


def messages(room):
    """回 [(日期, 第幾則, 標籤, 時間, 內文)]，最近 DAYS 天、依時間排。第幾則＝當天檔裡第幾個 ## 標題（跟匯出檔一致）。"""
    out = []
    for f in sorted(glob.glob(os.path.join(LOGS_DAY, room, "????-??-??.md")))[-DAYS:]:
        day = os.path.basename(f)[:-3]
        try:
            text = open(f, encoding="utf-8").read()
        except OSError:
            continue
        idx = 0
        for part in re.split(r"(?m)^(?=## )", text):
            if not part.startswith("## "):
                continue
            idx += 1
            head, _, body = part.partition("\n")
            m = HEAD.match(head.rstrip())
            label, t = (m.group(1), m.group(2)) if m else (head[3:].strip(), "")
            out.append((day, idx, label, t, body.strip()))
    return out


def kind(label):
    if label.startswith("旁聽"):
        return "旁聽"
    if label.startswith("使用者"):
        return "使用者"
    if label.startswith("系統"):
        return "系統"
    return "AI"


def speaker(label):
    return re.split(r"[（(｜]", label, 1)[0].strip()


def is_me(label, acc):
    return kind(label) == "AI" and speaker(label) in acc.get("發言名", [])


def counted(msg, acc):
    _, _, label, _, body = msg
    if is_me(label, acc) or kind(label) not in acc["只算"]:
        return False
    # 11-24 使用者：「加一個條件，自己未讀的訊息量」→ 標題含這些字的（例：旁聽自自己的視窗）不算未讀
    if any(k and k in label for k in acc.get("排除標題含", [])):
        return False
    return not any(k and k in body for k in acc.get("排除含", []))


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(PB, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(STATE + ".tmp", STATE)


def evaluate(name, acc, msgs, st):
    """回 dict：讀到、新增則數、會不會觸發、原因。"""
    me = st.get(name) or {}
    pos = tuple(me.get("讀到") or ())
    own = [(d, i) for d, i, lb, _, _ in msgs if is_me(lb, acc)]
    if own and (not pos or own[-1] > pos):
        pos, why_pos = own[-1], "你在房間發言"
    elif pos:
        why_pos = me.get("讀到原因", "上次累積喚醒")
    else:   # 第一次：沒有發言紀錄 → 從現在起算，不一開始就叫
        pos = (msgs[-1][0], msgs[-1][1]) if msgs else ("", 0)
        why_pos = "初次登記，從現在起算"
    new = [m for m in msgs if (m[0], m[1]) > pos and counted(m, acc)]
    left = acc["冷卻分鐘"] * 60 - (time.time() - float(me.get("上次推送", 0)))
    fire = len(new) >= acc["則數"] and left <= 0
    return {"讀到": list(pos), "讀到原因": why_pos, "新增": new, "門檻": acc["則數"],
            "冷卻剩分": max(0, round(left / 60)), "觸發": fire}


def summarize(new):
    """小 AI 摘要 3～5 行；回 (文字, 模型)。送前每則過隱私篩，不可送的整則不送。"""
    try:
        sys.path.insert(0, HERE)
        import 隱私篩
        lines = []
        for d, i, lb, t, body in new[-40:]:
            ok, cleaned, _ = 隱私篩.check(body)
            if ok:
                lines.append(f"[{t}] {speaker(lb)}（{kind(lb)}）：{cleaned[:300]}")
        if lines:
            sys.path.insert(0, os.path.join(SHARED, "ai-call"))
            import importlib
            pool = importlib.import_module("免費AI池")
            prompt = ("下面是團隊主討論的新訊息（一則一行）。用繁體中文寫 3～5 行摘要給沒讀到的 AI 同事："
                      "誰提了什麼、做了什麼決定、有沒有點名誰要做事。要涵蓋從第一則到最後一則（越新的越重要），"
                      "不要逐則翻譯；每行一句、不加標題、不要評論。\n\n" + "\n".join(lines)[:8000])
            for prov in ("groq", "nvidia"):
                try:
                    raw = (pool.ask(prov, prompt, max_tokens=600, timeout=40) or "").strip()
                    raw = re.sub(r"(?s)<think>.*?</think>", "", raw).strip()
                    if len(raw) >= 20:
                        rows = [x.strip() for x in raw.splitlines() if x.strip()]
                        if len(rows) < 3:   # 11-24：模型常回一整段不換行 → 照句號切開
                            rows = [x.strip() + "。" for x in re.split(r"[。！？]", " ".join(rows)) if x.strip()]
                        rows = [r if len(r) <= 120 else r[:118] + "…" for r in rows[:5]]
                        return "\n".join(rows), f"{prov}:{pool.PROVIDERS[prov][2]}"
                except Exception:
                    continue
    except Exception:
        pass
    rows = ["- %s %s：%s" % (t, speaker(lb), " ".join(body.split())[:30]) for d, i, lb, t, body in new[-15:]]
    if len(new) > 15:
        rows.insert(0, f"（只列最後 15 則，前面還有 {len(new) - 15} 則）")
    return "\n".join(rows), "退回列表"


def where_to_read(room, pos, new):
    """「請讀 <匯出檔> 的第 X 則之後」；跨天就逐檔列。"""
    days = sorted({m[0] for m in new})
    parts = []
    for d in days:
        p = os.path.join(EXPORT, room, d + ".md")
        if pos and d == pos[0]:
            parts.append(f"{p} 的第 {pos[1]} 則之後")
        else:
            parts.append(f"{p}（整天）")
    first = new[0]
    return "、".join(parts), f"## {first[2]}{first[3]}"


def build_text(name, acc, ev, summary, model):
    room = acc["房間"]
    loc, anchor = where_to_read(room, ev["讀到"], ev["新增"])
    return (f"【累積喚醒】{room}自你上次後新增 {len(ev['新增'])} 則，請讀 {loc}（第一則標題 `{anchor}`），跟上進度；"
            f"只需讀、有要回的再回。匯出每 10 分更新，最新幾則可能還沒進匯出檔。\n"
            f"摘要（{model}）：\n{summary}\n"
            f"（規則在 {acc['_檔']} 的 累積：門檻 {acc['則數']} 則、冷卻 {acc['冷卻分鐘']:g} 分；不想收就設 \"開\": false）")


def close_ticket(name):
    try:
        import window_agent as wa
        box = os.path.join(wa.HOMES[name], "群組", "待回")
        p = os.path.join(box, "派報-累積喚醒.md")
        if os.path.exists(p):
            os.makedirs(os.path.join(box, "已處理"), exist_ok=True)
            os.replace(p, os.path.join(box, "已處理", time.strftime("%m%d-%H%M%S-") + "派報-累積喚醒.md"))
    except Exception:
        pass


def run(dry=False, only=None, force=False, log=print):
    msgs_by_room, st, report = {}, load_state(), []
    for name, acc in rules().items():
        if only and name != only:
            continue
        room = acc["房間"]
        if room not in msgs_by_room:
            msgs_by_room[room] = messages(room)
        msgs = msgs_by_room[room]
        ev = evaluate(name, acc, msgs, st)
        if force and ev["新增"]:
            ev["觸發"] = True
        report.append((name, acc, ev))
        if dry:
            continue
        me = st.setdefault(name, {})
        if ev["讀到原因"] == "你在房間發言" and me.get("讀到") != ev["讀到"]:
            close_ticket(name)   # 它發言了＝讀過，視窗 agent 的累積喚醒單自動銷掉
        if not ev["觸發"]:
            if me.get("讀到") != ev["讀到"]:
                me.update(讀到=ev["讀到"], 讀到原因=ev["讀到原因"])
            continue
        t0 = time.time()
        summary, model = summarize(ev["新增"])
        text = build_text(name, acc, ev, summary, model)
        os.makedirs(PB, exist_ok=True)
        with open(REQ, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": time.strftime("%m-%d %H:%M"), "給": name, "原因": "累積喚醒", "內容": text},
                               ensure_ascii=False) + "\n")
        end = ev["新增"][-1]
        me.update(讀到=[end[0], end[1]], 讀到原因="上次累積喚醒", 上次推送=time.time())
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": time.strftime("%Y-%m-%d %H:%M"), "給": name, "房間": room, "則數": len(ev["新增"]),
                                "門檻": acc["則數"], "從": ev["讀到"], "到": [end[0], end[1]], "摘要模型": model,
                                "摘要秒": round(time.time() - t0, 1), "強制": bool(force)}, ensure_ascii=False) + "\n")
        log(f"累積喚醒：{name} {len(ev['新增'])} 則（門檻 {acc['則數']}）→ 叫醒請求，摘要 {model}")
    if not dry:
        save_state(st)
    return report


def stats():
    rows = []
    try:
        rows = [json.loads(x) for x in open(LEDGER, encoding="utf-8") if x.strip()]
    except (OSError, ValueError):
        pass
    if not rows:
        return "還沒有累積喚醒紀錄。"
    by = {}
    for r in rows:
        by.setdefault(r["給"], []).append(r)
    out = [f"累積喚醒共 {len(rows)} 次（{rows[0]['時間']} ～ {rows[-1]['時間']}）"]
    for name, rs in by.items():
        n = [r["則數"] for r in rs]
        models = {}
        for r in rs:
            models[r["摘要模型"]] = models.get(r["摘要模型"], 0) + 1
        days = {r["時間"][:10] for r in rs}
        out.append(f"- {name}：{len(rs)} 次、平均每天 {len(rs) / max(1, len(days)):.1f} 次；每次則數 平均 {sum(n) / len(n):.1f}、"
                   f"最多 {max(n)}；門檻 {rs[-1]['門檻']}；摘要 " + "、".join(f"{k} {v}" for k, v in models.items()))
    out.append("調門檻：一天叫太多次 → 調高 則數；每次則數遠超門檻 → 冷卻太長或掃太慢。")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "乾跑"
    if cmd == "統計":
        print(stats())
    elif cmd in ("乾跑", "跑", "強制"):
        only = sys.argv[2] if len(sys.argv) > 2 else None
        for name, acc, ev in run(dry=(cmd == "乾跑"), only=only, force=(cmd == "強制")):
            print(f"{name}：讀到 {ev['讀到'][0]} 第 {ev['讀到'][1]} 則（{ev['讀到原因']}）；之後新增 {len(ev['新增'])} 則"
                  f"／門檻 {ev['門檻']}；冷卻剩 {ev['冷卻剩分']} 分 → {'觸發' if ev['觸發'] else '不觸發'}")
    else:
        print(__doc__)
