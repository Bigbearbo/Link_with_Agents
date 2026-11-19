r"""方言轉譯：把各 AI 的「口癖」寫法轉成中樞看得懂的標準格式，並記帳。
使用者 11-19 14:38：「知道他的風格，可以寫個轉碼程式，自己看了也舒服，有異常再通知。有點像螺旋語的方言轉譯。」
Gemini 14:38：寬鬆收件但要記帳（RFC 9413：只容錯不記錄會讓協定腐化）；草稿不要誤發。
Codex 14:38：拿 45 張退件離線比對新舊 parser，不真的發群。

用法：
  python 方言轉譯.py 重播 <資料夾>     離線重播退件，看新 parser 會怎麼讀（不發群）
  python 方言轉譯.py 口癖              各 AI 口癖統計（logs/口癖.jsonl）
  python 方言轉譯.py 寫表              重寫 H:\Agent共用\方言轉換表.md（中樞每小時也會寫）
"""
import json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
QUIRK_LOG = os.path.join(HERE, "logs", "口癖.jsonl")
ROOM_ALIAS = {"互相討論": "g1004308131170", "互相討論群": "g1004308131170", "機器人互相討論群": "g1004308131170",
              "主討論": "main", "main": "main"}
TEXT_KEYS = ("text", "內容", "message", "content", "msg", "body", "reply", "回覆")
ROOM_KEYS = ("room", "房間", "chat", "room_id")
DRAFT = re.compile(r"草稿|draft|\.tmp$|^~|\.bak", re.I)


def clean_text(raw):
    sys.path.insert(0, HERE)
    import shared_watch as sw
    return sw.clean_text(raw)


def parse_send(fn, raw, rooms, last_room=None):
    """回傳 (room, text, quirks)。quirks＝這次用到的寬容規則（口癖），空的代表標準格式。
    rooms：現有房間 id 集合。讀不出內容時 text 為空。"""
    quirks = []
    if isinstance(raw, bytes):
        b = raw
        raw = clean_text(raw)
        if b.startswith(b"\xef\xbb\xbf") or b"\r\n" in b:   # 中文內文本來就用全形冒號，不算口癖
            quirks.append("怪符號（BOM／CRLF）")
    guess = "main" if re.search(r"main|主討論", fn, re.I) else ("g1004308131170" if re.search(r"g100|互相", fn) else None)
    d = None
    if fn.lower().endswith(".json") or raw.lstrip().startswith("{"):
        try:
            d = json.loads(raw)
        except ValueError:
            if fn.lower().endswith(".json"):
                quirks.append(".json 裡寫純文字")
    if isinstance(d, dict):
        rk = next((k for k in ROOM_KEYS if d.get(k)), None)
        tk = next((k for k in TEXT_KEYS if d.get(k)), None)
        rid = str(d.get(rk) or "") if rk else ""
        text = str(d.get(tk) or "").strip() if tk else ""
        if rk and rk != "room":
            quirks.append(f"房間欄叫 {rk}")
        if tk and tk != "text":
            quirks.append(f"內容欄叫 {tk}")
    else:
        first, _, rest = raw.partition("\n")
        has = re.match(r"\s*(房間|room)\s*[:=]\s*(\S+)", first, re.I)
        rid, text = (has.group(2) if has else ""), (rest if has else raw).strip()
    if rid in ROOM_ALIAS and ROOM_ALIAS[rid] != rid:
        quirks.append("房間寫中文名")
    rid = ROOM_ALIAS.get(rid, rid)
    if rid not in rooms:
        quirks.append("沒寫房間" if not rid else f"房間寫錯（{rid}）")
        rid = guess or last_room or "g1004308131170"
    return rid, text, quirks


def is_draft(fn):
    return bool(DRAFT.search(fn))


def log_quirks(agent, fn, quirks):
    """記一筆；回傳這位 agent 第一次出現的口癖（要通知）。"""
    if not quirks:
        return []
    seen = set()
    try:
        for line in open(QUIRK_LOG, encoding="utf-8"):
            r = json.loads(line)
            if r.get("agent") == agent:
                seen.update(r.get("quirks", []))
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.dirname(QUIRK_LOG), exist_ok=True)
    with open(QUIRK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%m-%d %H:%M:%S"), "agent": agent, "file": fn, "quirks": quirks},
                           ensure_ascii=False) + "\n")
    norm = lambda q: re.sub(r"（.*?）$", "", q)
    old = {norm(q) for q in seen}
    return [q for q in quirks if norm(q) not in old]


def stats():
    from collections import Counter
    c = Counter()
    try:
        for line in open(QUIRK_LOG, encoding="utf-8"):
            r = json.loads(line)
            for q in r.get("quirks", []):
                c[(r["agent"], re.sub(r"（.*?）$", "", q))] += 1
    except OSError:
        print("還沒有紀錄")
        return
    for (a, q), n in c.most_common():
        print(f"{a}\t{q}\t{n}")


TABLE = r"H:\Agent共用\方言轉換表.md"
RULES = [  # (口癖, 轉成什麼, 在哪裡處理)——改轉譯規則時同步改這張表（同一個檔，比較不會忘）
    (".json 檔裡寫純文字", "整份當發言內容", "發送箱"),
    ("沒寫房間／房間寫錯", "看檔名（main／互相）→ 最後叫它的房間 → 互相討論群", "發送箱"),
    ("房間寫中文名（主討論、互相討論群）", "換成房間 id（main、g1004308131170）", "發送箱"),
    ("內容欄叫 內容／message／content／msg／body／reply／回覆", "當成 text", "發送箱"),
    ("房間欄叫 房間／chat／room_id", "當成 room", "發送箱"),
    ("BOM、Windows／Mac 換行、零寬字元、不換行空白、智慧引號", "統一成一般字元", "發送箱、inbox"),
    ("全形冒號、`**from**:`、中文欄名（寄件人、給、房間）", "當成 from／to／room", "inbox 工單"),
    ("沒有 `---` 分隔線", "第一行不像欄位的就當內文", "inbox 工單"),
    ("`DOT_REQUEST_` 開頭沒寫 to", "交給 Claude", "inbox 工單"),
    ("沒寫給誰／亂碼（5 個以上 �）", "轉給輝達判斷要交給誰", "inbox 工單"),
    ("放在 inbox 的來源子資料夾（Dot／視窗Gemini／其他App）", "資料夾名當寄件人", "inbox 工單"),
    ("Google 文件 .gdoc", "**轉不了**：退件並在群組貼連結，請改存 .md／.txt", "inbox 工單"),
    ("檔名有 草稿／draft／.tmp", "**不發**（防誤發）", "發送箱"),
]


def write_table():
    """寫給大家看的轉換表（使用者 14:40：「怕自己忘記其實有轉碼，還是要留個紀錄，提醒大家有這麼個轉換表」）。"""
    from collections import Counter
    c, last = Counter(), {}
    try:
        for line in open(QUIRK_LOG, encoding="utf-8"):
            r = json.loads(line)
            for q in r.get("quirks", []):
                k = (r["agent"], re.sub(r"（.*?）$", "", q))
                c[k] += 1
                last[k] = r["t"]
    except (OSError, ValueError):
        pass
    out = ["# 方言轉換表（自動產生，勿手改）",
           "摘要：各 AI 的寫法習慣（口癖）由中樞程式自動轉成標準格式，不退件。這張表提醒大家「有這層轉換」：看到別人的原始檔怪怪的、群組卻貼得好好的，就是這裡轉的。",
           f"更新：{time.strftime('%Y-%m-%d %H:%M')}｜來源程式：`H:\\Claude用\\三方討論\\方言轉譯.py`（inbox 工單的部分在 `shared_watch.py` parse_ticket）",
           "原因：使用者 11-19 14:36～14:40「有口癖的 AI，應該用程式幫他放寬，而不是說他報告格式不對」「模型腦袋已經固定，再加一條指令不如程式轉換」。",
           "", "## 轉換規則", "| 口癖 | 轉成 | 在哪裡 |", "|---|---|---|"]
    out += [f"| {a} | {b} | {w} |" for a, b, w in RULES]
    out += ["", "## 誰有哪些口癖（logs/口癖.jsonl 統計）", "| AI | 口癖 | 次數 | 最近一次 |", "|---|---|---|---|"]
    out += [f"| {a} | {q} | {n} | {last[(a, q)]} |" for (a, q), n in c.most_common()] or ["| （還沒有紀錄） | | | |"]
    out += ["", "## 給大家", "- 自己送出時還是照正式格式寫（寬進嚴出）；這層轉換是保險，不是鼓勵亂寫。",
            "- 某種口癖很常見 → 考慮固化成正式格式；轉錯一次 → 記進 `規則反例.md`，再改程式。",
            "- 想加新的轉換：在群組 @Claude 說明口癖長什麼樣、附原始檔。"]
    open(TABLE, "w", encoding="utf-8").write("\n".join(out) + "\n")
    return TABLE


def replay(folder):
    rooms = {"main", "g1004308131170", "solo-nvidia", "solo-gemini"}
    ok = bad = 0
    from collections import Counter
    kinds, where = Counter(), Counter()
    for fn in sorted(os.listdir(folder)):
        if fn.endswith(".原因.txt") or not fn.lower().endswith((".json", ".txt")):
            continue
        rid, text, q = parse_send(re.sub(r"^\d{4}-\d{6}-", "", fn), open(os.path.join(folder, fn), "rb").read(), rooms)
        if text:
            ok += 1
            where[rid] += 1
        else:
            bad += 1
            print("讀不出：", fn)
        for x in q:
            kinds[re.sub(r"（.*?）$", "", x)] += 1
    print(f"重播 {ok + bad} 張：讀得出 {ok}、讀不出 {bad}；房間 {dict(where)}")
    print("口癖：", dict(kinds))


if __name__ == "__main__":
    a = sys.argv[1:] or ["口癖"]
    if a[0] == "重播":
        replay(a[1])
    elif a[0] == "寫表":
        print(write_table())
    else:
        stats()
