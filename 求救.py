r"""AI 卡關求救通道（中樞入口）。使用者 11-20 04:39：「錯誤求救系統，自己卡住了，弄個通道呼叫大家來幫忙（有順序和條件），
還要有資訊篩選系統，例如加入哪些上下文給小 AI，目標是有效率的幫忙解救，AI 用的 DEBUG 系統」。規格：Codex 04:41 `專案\小模型池\AI卡關求救.md`。

AI 發言行首寫「求救」，建議格式（每項一行，缺關鍵欄位只追問一項）：
  求救 #待辦號（可省）
  卡在：……
  預期：……
  實際：……
  試過：……
  證據：路徑或訊息時間（最多兩個）

分流（handle 回傳要貼回房間的一行）：
  0. 同一張卡（同待辦號，或卡在＋實際 相同）→ 補進原卡，不重叫。
  1. 重大（資料不見／中樞掛了／整個服務中斷／封存異常）→ 不問小 AI，直接 @負責人＋@Claude。
  2. 一般 → 過隱私篩 → 只問一個免費小 AI（groq），窄問題＋預期／實際＋最多兩段摘錄（≤ 約 1,200 字），
     回「可能原因／下一個沒有副作用的檢查／不確定」；不給它修改權，回覆只當候選。
  3. 同一張卡第二次求救（小 AI 的建議沒用）→ 換另一家（cloudflare）只給症狀、不給前人假設，獨立解釋；並 @Claude。
  卡片存 logs\求救卡.json；結案：「求救 #卡號 解決：怎麼解的」。
"""
import hashlib, importlib, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(HERE, "logs", "求救卡.json")
SEVERE = re.compile(r"資料不見|資料遺失|中樞(掛|當|死)|整個(服務|系統)(中斷|掛)|封存異常|檔案被刪|備份壞")
FIELDS = ("卡在", "預期", "實際", "試過")


def load():
    try:
        return json.load(open(CARDS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(c):
    os.makedirs(os.path.dirname(CARDS), exist_ok=True)
    json.dump(c, open(CARDS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def parse(text):
    body = re.sub(r"^\s*求救\s*", "", text)
    f = {k: (re.search(rf"{k}[：:]\s*(.+)", body) or [None, ""])[1].strip() for k in FIELDS + ("證據", "解決")}
    m = re.search(r"#(\d+)", body.splitlines()[0] if body else "")
    f["待辦"] = m.group(1) if m else ""
    f["原文"] = body.strip()
    return f


def owner_of(todo_id):
    try:
        for t in json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8")):
            if str(t.get("id")) == todo_id:
                return str(t.get("負責", "")).split("＋")[0] or "Claude"
    except (OSError, ValueError):
        pass
    return "Claude"


def ask_small(name, f, with_history=True):
    ys = importlib.import_module("隱私篩")
    core = f"卡在：{f['卡在']}\n預期：{f['預期']}\n實際：{f['實際']}" + (f"\n已試過：{f['試過']}" if with_history else "")
    ok, safe, why = ys.check(core[:1200])
    if not ok:
        return None, why
    sys.path.insert(0, r"H:\Agent共用\ai-call")
    pool = importlib.import_module("免費AI池")
    prompt = ("一個 AI 同事卡住了，請幫忙除錯。只根據下面的資訊判斷，不要假設看不到的東西。\n" + safe +
              "\n用繁體中文回三行：\n可能原因：（一句）\n下一個檢查：（一個不會改動任何東西、能分辨原因的檢查）\n不確定的地方：（一句）")
    return pool.ask(name, prompt, max_tokens=800, timeout=60).strip(), ""


def handle(who, text):
    f = parse(text)
    cards = load()
    key = f["待辦"] or hashlib.sha1((f["卡在"] + f["實際"]).encode()).hexdigest()[:8]
    cid = "求救-" + key
    card = cards.get(cid)
    now = time.strftime("%m-%d %H:%M")
    if f["解決"] and card:
        card.update({"狀態": "已解決", "解法": f["解決"], "結案": now})
        save(cards)
        return f"✅ {cid} 結案：{f['解決'][:80]}（解法已記在 logs\\求救卡.json，之後同類問題會先查這裡）"
    if card and card.get("狀態") == "已解決" and not f["卡在"]:
        return f"這張 {cid} 之前解過：{card.get('解法', '')[:120]}"
    missing = [k for k in ("卡在", "實際") if not f[k]]
    if missing and not card:
        return f"🆘 收到 {who} 的求救，但缺「{missing[0]}」——請再發一次，用一行寫「{missing[0]}：……」。"
    if card:   # 0／3：同一張卡再求救
        card.setdefault("紀錄", []).append({"時間": now, "誰": who, "內容": f["原文"][:400]})
        card["次數"] = card.get("次數", 1) + 1
        save(cards)
        if card["次數"] == 2 and card.get("等級") == "一般":
            r, why = ask_small("cloudflare", {**card["欄位"], **{k: v for k, v in f.items() if v}}, with_history=False)
            tail = f"\n🔁 換一家獨立看（只給症狀，不給前人假設）：\n{r}" if r else f"（{why}，沒送外部）"
            return f"🆘 {cid} 第 2 次求救，已補進原卡。@Claude 請看一下。{tail}"
        return f"🆘 {cid} 已補進原卡（第 {card['次數']} 次）；負責：{card['負責']}，不重複叫人。"
    severe = bool(SEVERE.search(f["原文"]))
    owner = owner_of(f["待辦"]) if f["待辦"] else "Claude"
    cards[cid] = {"建立": now, "發起": who, "負責": owner, "等級": "重大" if severe else "一般", "狀態": "處理中",
                  "欄位": {k: f[k] for k in FIELDS + ("證據",)}, "次數": 1, "紀錄": []}
    save(cards)
    if severe:   # 1
        return f"🚨 {cid}（重大）：{f['卡在'][:60]}——不問小 AI，直接 @{owner}" + ("" if owner == "Claude" else " @Claude") + " 處理。"
    r, why = ask_small("groq", f)   # 2
    if not r:
        return f"🆘 {cid}：{why}，不送外部小 AI；@{owner} 請直接看。"
    cards[cid]["小AI建議"] = r
    save(cards)
    return (f"🆘 {cid}（一般）收到 {who} 的求救。先問了一家小 AI（groq，只給症狀與預期／實際）——**只是候選，請自己驗證**：\n{r}\n"
            f"沒用的話再發一次「求救 {('#' + f['待辦']) if f['待辦'] else ''}」並寫試了什麼，會換另一家獨立看並叫 @Claude；解決了發「求救 … 解決：怎麼解的」。")


if __name__ == "__main__":
    print(handle("測試", " ".join(sys.argv[1:]) or "求救\n卡在：出題程式跑完 groq 回空白\n預期：回 JSON\n實際：連兩次空字串\n試過：加長 max_tokens"))
