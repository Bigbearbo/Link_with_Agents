r"""意思確認：回看對話紀錄，找出「使用者這句話 AI 可能沒搞懂」的地方；兩家 AI 都判斷意思有分歧才變成待討論，讓使用者補充。
使用者 11-20 02:56：「研究我們的對話紀錄，發現有點搞不清楚意思（例如我說的），你們討論後確認意思存在分歧未定，可以變成待討論，讓我補充」

流程：
  1. 篩（Haiku）：讀某房某天的切片，每則使用者訊息連同後面 AI 怎麼回，挑出「可能有兩種以上理解、或 AI 回的方向跟原話對不上」的，附兩種理解。
  2. 複核（另一家：OpenAI 贈點；主討論是私人對話，不送免費模型）：獨立看同一段，判斷「確實不明」還是「其實清楚／後面已經講清楚了」。
  3. 兩家都說不明 → 開一張「待討論：意思確認｜…」，紀錄附原話、兩種理解、【AI建議】；使用者在待討論頁補一句就好。
     一家說不明 → 只記在 logs\意思確認.md，不打擾使用者。
  每次最多開 3 張，避免洗版；已經被使用者自己更正過（後面有「我是說／不是這個意思」）的不重開。

用法：PY 意思確認.py [房間] [日期]     預設 主討論、今天
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SLICES = os.path.join(HERE, "logs-按日")
REPORT = os.path.join(HERE, "logs", "意思確認.md")
TODO = r"H:\Agent共用\待辦\待辦.py"
sys.path.insert(0, r"H:\Agent共用\ai-call")
MAX_NEW = 3


def messages(room, day):
    text = open(os.path.join(SLICES, room, f"{day}.md"), encoding="utf-8").read()
    out = []
    for blk in re.split(r"\n## ", text)[1:]:
        head, _, body = blk.partition("\n")
        m = re.match(r"([^（(\s]+).*?(\d\d-\d\d \d\d:\d\d)", head)
        if m:
            out.append({"who": m.group(1), "time": m.group(2), "text": body.strip()})
    return out


def windows(msgs):
    """每則夠長的使用者訊息 + 後面 AI 的回應（截短）+ 下一則使用者訊息。"""
    out = []
    for i, m in enumerate(msgs):
        if m["who"] != "使用者" or len(m["text"]) < 15:
            continue
        after, nxt = [], ""
        for n in msgs[i + 1:i + 8]:
            if n["who"] == "使用者":
                nxt = n["text"][:200]
                break
            after.append(f"{n['who']}：{n['text'][:350]}")
        out.append({"time": m["time"], "原話": m["text"][:500], "AI回應": after[:3], "下一則使用者": nxt})
    return out


def ask_json(name, prompt, tokens=2500):
    import importlib
    pool = importlib.import_module("免費AI池")
    for _ in range(2):
        raw = pool.ask(name, prompt, max_tokens=tokens, timeout=120, paid=True)
        m = re.search(r"\{.*\}|\[.*\]", raw or "", re.S)
        try:
            return json.loads(m.group(0))
        except (ValueError, AttributeError):
            continue
    return None


SCREEN = """下面是使用者和幾個 AI 的對話片段。請找出「使用者原話可能有兩種以上理解，而且 AI 的回應可能選錯或沒說清楚選哪個」的片段。
口語、錯字、省略本身不算；要的是**意思真的可能分岔、而且會影響做法**的。使用者下一則已經自己澄清的不算。寧缺勿濫，最多挑 5 個。
只回 JSON 陣列：[{{"time": "MM-DD HH:MM", "理解A": "", "理解B": "", "AI選了": "A／B／沒說清楚", "為什麼可能分岔": "一句"}}]，沒有就回 []。
片段：
{data}"""

REVIEW = """使用者說：「{orig}」（{time}）
AI 們後來的回應：
{after}
使用者下一則：{nxt}
有人認為這句可能有兩種理解：A＝{a}；B＝{b}。
請獨立判斷：這句的意思在當時是「確實不明、需要問使用者」，還是「其實清楚」或「後面已經講清楚了」？
只回 JSON：{{"不明": true 或 false, "理由": "一句", "建議問法": "如果不明，用一句白話問使用者"}}"""


def run(room="主討論", day=None):
    day = day or time.strftime("%Y-%m-%d")
    ws = windows(messages(room, day))
    found = []
    for i in range(0, len(ws), 15):   # 每批 15 則
        batch = ws[i:i + 15]
        r = ask_json("anthropic", SCREEN.format(data=json.dumps(batch, ensure_ascii=False, indent=0)))
        if isinstance(r, list):
            found += r
    by_time = {w["time"]: w for w in ws}
    rows, opened = [], 0
    for c in found:
        w = by_time.get(c.get("time"))
        if not w:
            continue
        rv = ask_json("openai", REVIEW.format(orig=w["原話"], time=w["time"], after="\n".join(w["AI回應"]) or "（無）",
                                              nxt=w["下一則使用者"] or "（無）", a=c.get("理解A"), b=c.get("理解B")), 600) or {}
        both = bool(rv.get("不明"))
        rows.append(f"| {w['time']} | {w['原話'][:40].replace('|', '／')} | A：{c.get('理解A', '')[:40]}／B：{c.get('理解B', '')[:40]} | "
                    f"Haiku：不明｜OpenAI：{'不明' if both else '清楚'}（{str(rv.get('理由', ''))[:40]}） | {'開待討論' if both and opened < MAX_NEW else '只記錄'} |")
        if both and opened < MAX_NEW:
            opened += 1
            title = f"待討論：意思確認｜{w['time']}「{w['原話'][:24]}…」"
            out = subprocess.run([sys.executable, "-X", "utf8", TODO, "加", title, "使用者", "中"], capture_output=True, text=True, encoding="utf-8").stdout
            nid = (re.search(r"#?(\d+)", out) or [None, ""])[1]
            if nid:
                note = (f"【請使用者看】你在 {room} {w['time']} 說：「{w['原話'][:200]}」。我們讀出兩種意思——A：{c.get('理解A')}；B：{c.get('理解B')}。"
                        f"當時 AI 選了 {c.get('AI選了')}。【AI建議】{rv.get('建議問法') or '請補一句是 A 還是 B'}")
                subprocess.run([sys.executable, "-X", "utf8", TODO, "做", nid, note], capture_output=True, text=True, encoding="utf-8")
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"\n## {room} {day}（跑於 {time.strftime('%m-%d %H:%M')}；看了 {len(ws)} 則使用者訊息，Haiku 挑出 {len(found)}，開待討論 {opened}）\n"
                "| 時間 | 原話 | 兩種理解 | 兩家判斷 | 處理 |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    return len(ws), len(found), opened, rows


if __name__ == "__main__":
    a = sys.argv[1:]
    n, f, o, rows = run(*(a[:2] if a else []))
    print(f"看了 {n} 則、挑出 {f}、開待討論 {o}")
    print("\n".join(rows))
