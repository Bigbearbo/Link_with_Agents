r"""報錯接線生（第一線客服）：使用者 11-20 02:42「報錯接線生也可以上線，當成客服，決定回覆還是要上報，應該可以增加系統磨合速度」
分流規則用 Codex 02:43 的 support_triage.triage（不重寫）；這支負責「把一句白話報錯變成 triage 要的欄位」並把結果接回聊天室與待辦。

怎麼觸發：任何房間裡使用者的訊息以「報錯」開頭（中樞呼叫 handle）。
流程：
  1. 抽欄位：Haiku（贈點，paid=True；先過隱私篩：可送用免費 groq，否則 Haiku）讀報錯，回 摘要／哪個功能／有沒有重現步驟／緊不緊急／缺什麼。
     抽不出來就退回關鍵字規則，不會卡住。
  2. 找負責人：依關鍵字（網頁、用量、中樞→Claude；訓練、學生、課程→Codex；研究觀察→Gemini；輝達、OpenCode→Claude…）。
  3. 找舊單：跟沒完成的待辦標題比關鍵字，重疊夠多就當「同一個問題已有人在修」。
  4. triage 決定：回覆／併進舊單／上報負責 AI（開新待辦）／只問最缺的一項。
  5. 每筆記 logs\報錯.jsonl（之後量磨合速度：從報錯到修好多久、幾成要上報）。
"""
import importlib.util, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TODO = r"H:\Agent共用\待辦\待辦.py"
LOG = os.path.join(HERE, "logs", "報錯.jsonl")
OWNERS = [("Codex", ("訓練", "學生", "課程", "codex", "Codex", "實驗")),
          ("Gemini", ("研究觀察", "Gemini", "agy")),
          ("Claude", ("網頁", "頁", "按鈕", "用量", "中樞", "書籤", "待討論", "研究頁", "設定", "跑版", "手機", "輝達", "OpenCode", "Telegram"))]


def _triage():
    sp = importlib.util.spec_from_file_location("st", r"H:\Codex用\outputs\roundtable-updates\support_triage.py")
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m.triage


def extract(text):
    """回 dict：摘要、功能、has_reproduction、urgent、missing_detail。"""
    fallback = {"摘要": text[:60], "功能": "", "has_reproduction": bool(re.search(r"(按|點|打開|輸入).{0,12}(就|後|會|結果)", text)),
                "urgent": bool(re.search(r"(全部|都不能|整個|打不開|掛了|當掉|資料不見)", text)), "missing_detail": "在哪一頁、按了什麼、看到什麼"}
    try:
        sys.path.insert(0, r"H:\Agent共用\ai-call")
        pool = importlib.import_module("免費AI池")
        prompt = ("你是客服接線生。把使用者報錯整理成 JSON，只回 JSON，不要註解。欄位：\n"
                  "摘要：一句話；功能：哪個頁面或功能；has_reproduction：布林，說清楚「在哪裡、做了什麼或看到什麼」就算 true"
                  "（畫面跑版只要講出哪一頁、哪個東西怎樣就算）；urgent：布林，只有整個系統不能用或資料不見才算 true；"
                  "missing_detail：要交給工程師還缺的那一項，沒缺就空字串。\n範例："
                  '{"摘要": "", "功能": "", "has_reproduction": false, "urgent": false, "missing_detail": ""}\n報錯：' + text)
        raw = ""
        # 使用者 11-20 03:00：主討論一般內容可送免費模型，有隱私的本機篩掉 → 先過隱私篩，可送就用免費 groq，不可送才用 Haiku 贈點
        sp = importlib.util.spec_from_file_location("ys", os.path.join(HERE, "隱私篩.py"))
        ys = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(ys)
        ok, safe, _why = ys.check(text)
        who, paid = ("groq", False) if ok else ("anthropic", True)
        if ok:
            prompt = prompt.replace(text, safe)
        for _ in range(2):   # 偶爾回壞 JSON：再問一次
            raw = pool.ask(who, prompt, max_tokens=600, timeout=40, paid=paid)
            m = re.search(r"\{.*\}", raw or "", re.S)
            try:
                j = json.loads(m.group(0))
                break
            except (ValueError, AttributeError):
                j = None
        if j is None:
            raise ValueError("抽不出 JSON：" + str(raw)[:80])
        for k in ("has_reproduction", "urgent"):
            j[k] = bool(j.get(k))
        return {**fallback, **j}
    except Exception:
        return fallback


def owner_of(text):
    for who, keys in OWNERS:
        if any(k in text for k in keys):
            return who
    return "Claude"


def open_issue(text):
    try:
        todos = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
    except (OSError, ValueError):
        return None
    words = set(re.findall(r"[\u4e00-\u9fff]{2}|[A-Za-z]{3,}", text))
    best, score = None, 0
    for t in todos:
        if t.get("狀態") == "完成":
            continue
        tw = set(re.findall(r"[\u4e00-\u9fff]{2}|[A-Za-z]{3,}", str(t.get("標題", ""))))
        s = len(words & tw)
        if s > score:
            best, score = t, s
    return str(best["id"]) if best is not None and score >= 3 else None


def todo(*args):
    try:
        return subprocess.run([PY, "-X", "utf8", TODO, *args], capture_output=True, text=True, encoding="utf-8", timeout=30).stdout.strip()
    except Exception as e:
        return f"（待辦寫入失敗：{type(e).__name__}）"


def handle(text, room_name, when):
    """回傳要貼回房間的一行說明。"""
    body = re.sub(r"^報錯[：:\s]*", "", text).strip()
    f = extract(body)
    who = owner_of(body + " " + str(f.get("功能", "")))
    issue = open_issue(body)
    report = {"urgent": f["urgent"], "has_reproduction": f["has_reproduction"], "check": "unverified",
              "owner": who, "open_issue": issue, "missing_detail": f.get("missing_detail") or ""}
    d = _triage()(report)
    act = d["action"]
    tag = f"報錯 {when}（{room_name}）：{f['摘要']}"
    if act == "attach":
        todo("做", issue, f"報錯線索：{tag}")
        msg = f"🎧 報錯接線生：這跟待辦 #{issue} 是同一件事，已把你的線索併進去。"
    elif act == "escalate":
        out = todo("加", f"【報錯】{f['摘要']}", who, "高" if f["urgent"] else "中")
        nid = (re.search(r"#(\d+)", out) or re.search(r"(\d+)", out) or [None, "?"])[1]
        msg = f"🎧 報錯接線生：已上報 @{who}，開成待辦 #{nid}（{'緊急' if f['urgent'] else '有重現步驟'}）。"
    elif act == "reply":
        msg = "🎧 報錯接線生：" + d["text"]
    else:
        msg = f"🎧 報錯接線生：收到。為了交給對的 AI，想再問一項：{f.get('missing_detail') or '在哪一頁、按了什麼、看到什麼'}？（回覆時開頭一樣寫「報錯」）"
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"時間": time.strftime("%Y-%m-%d %H:%M:%S"), "房間": room_name, "原文": body[:500], "欄位": f,
                             "負責": who, "舊單": issue, "決定": d}, ensure_ascii=False) + "\n")
    return msg


if __name__ == "__main__":   # 試跑：PY 報錯接線生.py "報錯 手機版待討論頁按鈕被切掉"（只印決定，不寫待辦）
    t = " ".join(sys.argv[1:]) or "報錯 手機版待討論頁上面的按鈕被切掉一半"
    b = re.sub(r"^報錯[：:\s]*", "", t)
    f = extract(b)
    rep = {"urgent": f["urgent"], "has_reproduction": f["has_reproduction"], "check": "unverified",
           "owner": owner_of(b + str(f.get("功能", ""))), "open_issue": open_issue(b), "missing_detail": f.get("missing_detail") or ""}
    print(json.dumps({"欄位": f, "送分流": rep, "決定": _triage()(rep)}, ensure_ascii=False, indent=1))
