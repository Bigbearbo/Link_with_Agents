r"""免費小 AI 在聊天室被點名時，先讀該讀的再說話（使用者 11-20 06:11：「幫 groq 在視窗聊天的時候讀入該有的資訊再來說話。
其他小 AI 也照做一下，分兩類，次數和用量型」）。

做法：
  1. 等同一房的大 AI 這輪講完（room.busy 變 False，最多等 WAIT 秒），讓小 AI 看得到他們剛查到的東西。
  2. 組上下文，四塊依序塞，塞到該家的字數預算為止：
     ① 使用者最後一句（全文） ② 簡寫白話對照（H:\Agent共用\簡寫白話.md，只放對話裡出現的詞）
     ③ 最近對話（大 AI 的回覆優先、每則截短） ④ 共用區／討論房檢索命中的片段（檢索\index.sqlite）
  3. 全部再過隱私篩；不過就不送。

兩類（預算＝整段上下文字數）：
  次數型：照呼叫次數算額度，上下文長一點不多花錢——groq（TPM 8000，所以壓在 5000 字）、google、cohere、nvidia（NIM）
  用量型：照 token／neurons 算，上下文越長越貴——cloudflare（每日 10,000 neurons）、anthropic／openai 贈點
"""
import os, re, sqlite3, time

KIND = {"groq": ("次數", 5000), "google": ("次數", 12000), "cohere": ("次數", 8000), "nvidia": ("次數", 8000),
        "cloudflare": ("用量", 2500), "anthropic": ("用量", 3000), "openai": ("用量", 3000)}
GLOSS = r"H:\Agent共用\簡寫白話.md"
INDEX = r"H:\Agent共用\檢索\index.sqlite"
WAIT = 240


def budget(name):
    return KIND.get(name, ("用量", 2500))


def wait_room(room):
    time.sleep(3)   # 讓這輪的接力先開始
    end = time.time() + WAIT
    while getattr(room, "busy", False) and time.time() < end:
        time.sleep(3)


def glossary(text):
    try:
        lines = [l for l in open(GLOSS, encoding="utf-8") if l.startswith("- **")]
    except OSError:
        return []
    out = []
    for l in lines:
        m = re.match(r"- \*\*(.+?)\*\*", l)
        keys = re.split(r"[／/]", m.group(1)) if m else []
        if any(k and not k.strip().isdigit() and k.lower() in text.lower() for k in keys):
            out.append(l.strip())
    return out


def terms(text):
    """從使用者那句挑檢索詞：英數詞（≥3）＋中文片段（3–6 字，切在標點／虛詞上）。"""
    t = re.sub(r"@\w+", " ", text)
    eng = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", t)
    grams = []   # 索引是 trigram：中文切成三字一組，全部 OR 起來讓 bm25 排
    for seg in re.split("[^" + chr(0x4e00) + "-" + chr(0x9fff) + "0-9]+|[的了嗎呢是在有和跟也就都還要會這那我你他們吧啊個比只]", t):
        grams += [seg[i:i + 3] for i in range(len(seg) - 2)]
    return list(dict.fromkeys(eng + grams))[:16]


def search(text, limit=4):
    ws = terms(text)
    if not ws or not os.path.exists(INDEX):
        return []
    hits = {}
    try:
        db = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, timeout=5)
        q = " OR ".join('"' + w.replace('"', '""') + '"' for w in ws)
        try:
            rows = db.execute("SELECT place, title, text, bm25(docs) FROM docs WHERE docs MATCH ? AND source != 'GPT對話' "
                              "ORDER BY bm25(docs) LIMIT 12", [q]).fetchall()
        except sqlite3.Error:
            rows = []
        for place, title, body, score in rows:
            body = body or ""
            i = max([body.find(w) for w in ws] + [0])
            snip = body[max(0, i - 120): i + 280].replace("\n", " ")
            if place not in hits or score < hits[place][0]:
                hits[place] = (score, f"〔{title or place}〕…{snip}…")
        db.close()
    except sqlite3.Error:
        return []
    return [v for _, v in sorted(hits.values())][:limit]


def build(room, name):
    """回傳要送給小 AI 的上下文（還沒過隱私篩）。"""
    kind, cap = budget(name)
    t = [m for m in room.transcript if m.get("who") != "系統"]
    last_user = next((m for m in reversed(t) if m.get("who") == "使用者"), {})
    q = (last_user.get("text") or "").strip()
    i0 = max((i for i, m in enumerate(t) if m is last_user), default=len(t))
    after = [m for m in t[i0 + 1:] if not str(m.get("who", "")).startswith("小AI")]   # 這輪大 AI 的回覆
    before = t[max(0, i0 - 6):i0]
    per = 500 if kind == "次數" else 220
    parts = [f"【使用者最後一句】\n{q}"]
    g = glossary(" ".join([q] + [m.get("text") or "" for m in after + before]))
    if g:
        parts.append("【簡寫白話】\n" + "\n".join(g))
    if after:
        parts.append("【這輪其他 AI 已經查到／回答的】\n" + "\n".join(f"{m.get('who')}：{(m.get('text') or '')[:per * 2]}" for m in after))
    if before:
        parts.append("【前面的對話】\n" + "\n".join(f"{m.get('who')}：{(m.get('text') or '')[:per]}" for m in before))
    if kind == "次數":
        s = search(q)
        if s:
            parts.append("【共用區檢索到的相關片段（可能過時，僅供參考）】\n" + "\n".join(s))
    import importlib
    ys = importlib.import_module("隱私篩")
    kept = []
    for p in parts:   # 每塊各自過隱私篩：哪塊不能送就整塊拿掉，不讓一段檢索片段害整則不能問
        head, _, body = p.partition("\n")
        lines = [l for l in body.split("\n") if ys.check(l, max_len=cap)[0]] if head != "【使用者最後一句】" else [body]
        if lines:
            kept.append(head + "\n" + "\n".join(ys.check(l, max_len=cap)[1] for l in lines))
    parts = kept
    out = ""
    for p in parts:   # 照優先順序塞，超過預算就截
        if len(out) + len(p) > cap:
            out += "\n\n" + p[:max(0, cap - len(out) - 4)]
            break
        out += ("\n\n" if out else "") + p
    return out, kind


def identity(name):
    """使用者 11-20 06:33：「他不知道他是 groq？」——groq 是平台名，模型是 gpt-oss-120b；告訴它兩者，被問到自己就用「我」。"""
    try:
        import importlib, sys
        sys.path.insert(0, r"H:\Agent共用\ai-call")
        p = importlib.import_module("免費AI池").PROVIDERS.get(name)
        return f"{p[0]} 平台上的 {p[2]} 模型" if p else name
    except Exception:
        return name


def prompt(name, ctx, kind):
    return (f"你是「小AI·{name}」：{identity(name)}（{kind}型額度）。聊天室裡大家叫你 {name}；"
            f"有人談到「{name}」就是在說你，請用「我」回答，不要用第三人稱評論自己。\n"
            f"你在一個多個 AI 一起工作的聊天室，使用者點名你。下面是程式替你整理的資料：使用者最後一句、"
            "簡寫白話、這輪其他 AI 的回答、前面對話" + ("、共用區檢索片段" if kind == "次數" else "") + "。\n"
            "規則：只根據這些資料回答，用繁體中文、200 字內，直接回應使用者最後一句；其他 AI 已經答過的不要重複，"
            "能補充、指出矛盾或提出不同看法才講；資料裡沒有的就說「資料裡沒有」，不要猜。\n\n" + ctx)


if __name__ == "__main__":
    import sys
    print(terms(" ".join(sys.argv[1:]) or "乘3是乘法*3?這個比乘二難，二進位乘二只要位移"))
    for s in search(" ".join(sys.argv[1:]) or "乘3是乘法*3?這個比乘二難，二進位乘二只要位移"):
        print(s[:200])
