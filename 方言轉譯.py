"""方言轉譯：把各 AI 的「口癖」寫法轉成中樞看得懂的標準格式，並記帳。
使用者 11-19 14:38：「知道他的風格，可以寫個轉碼程式，自己看了也舒服，有異常再通知。有點像螺旋語的方言轉譯。」
Gemini 14:38：寬鬆收件但要記帳（RFC 9413：只容錯不記錄會讓協定腐化）；草稿不要誤發。
Codex 14:38：拿 45 張退件離線比對新舊 parser，不真的發群。

用法：
  python 方言轉譯.py 重播 <資料夾>     離線重播退件，看新 parser 會怎麼讀（不發群）
  python 方言轉譯.py 口癖              各 AI 口癖統計（logs/口癖.jsonl）
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
    else:
        stats()
