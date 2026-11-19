r"""權限放寬提案：被拒絕的不重複問使用者；AI 可以提案放寬，另一位 AI 審核通過就生效。
使用者 11-19 14:45：「申請不通過的記得不用太過重複，給他們另一個申請管道，可以改審查程式，自己去放寬，其他 AI 再審核審查程式。」

在群組發言（中樞知道是誰說的，名字冒充不了）：
  放寬提案：DeepSeek 可寫 H:\Agent共用\研究想法庫｜理由：要直接寫想法檔
  核可放寬 #3            （提案人和受益人以外的 AI 才能核可）
  否決放寬 #3 理由……
紅線（誰核可都不行）：金鑰／secrets、ai-call、記憶、導師助手、中樞程式資料夾、H:\Agent共用 根目錄本身、別人的家。
想改的是「審查規則本身」（例如紅線、哪些副檔名擋）：把改法寫成 `H:\Agent共用\權限\審查程式提案\<名字>-<日期>.md`，
另一位 AI 在檔尾寫審核意見，Claude 照兩邊意見改程式（git 一個 commit，可撤銷）。
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "logs", "權限放寬提案.json")
PERM_LOG = os.path.join(HERE, "agent-權限-紀錄.jsonl")
AIS = {"Claude", "Codex", "Gemini", "輝達", "DeepSeek"}          # 本機、有身分驗證的 AI；App（Dot、視窗Gemini）可提案、不能核可
HOMES = {"Gemini": r"H:\Gemini用", "輝達": r"H:\免費模型用", "DeepSeek": r"H:\DeepSeek用"}
RED = ("secret", "token", "tg_state", ".dpapi", "web-key", "credential", "password", "金鑰",
       "ai-call", "記憶", "導師助手", "三方討論", "catalog.py", "待辦.json")
DENY_QUIET = 24 * 3600   # 同一位、同一項被拒絕後 24 小時內不再問使用者


def _load():
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"next": 1, "items": {}}


def _save(d):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(d, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def red_line(path, target):
    """回傳違反的紅線（空字串＝可以）。"""
    if ".." in path or any(c in path for c in "*?<>\"") or not re.match(r"^[A-Za-z]:\\", path):
        return "路徑要寫完整（例 H:\Agent共用\某資料夾），不能有 ..、萬用字元"   # Gemini 14:47：防越界
    p = os.path.realpath(path).lower()
    if any(r.lower() in p for r in RED):
        return "碰到紅線字詞（金鑰、ai-call、記憶、導師助手、中樞程式…）"
    if p.rstrip("\\") in (r"h:\agent共用", "h:", "h:\\"):
        return "不能放整個共用區根目錄，只能放子資料夾"
    for who, home in HOMES.items():
        if who != target and p.startswith(home.lower()):
            return f"那是 {who} 的家"
    if not (p.startswith(r"h:\agent共用" + "\\") or p.startswith(HOMES.get(target, "\0").lower())):
        return "只能放 H:\\Agent共用 的子資料夾或自己的家"
    return ""


def recently_denied(agent, items):
    """同一位、同一批項目被拒絕過、還在冷卻期 → 回傳那次的時間；沒有回 None。
    冷卻：拒絕 1 次 24 小時；拒絕 2 次以上 7 天（Codex 14:48：同案沒有新理由，不因時間到了就重新問）。"""
    want = sorted(items)
    n_denied = 0
    try:
        lines = open(PERM_LOG, encoding="utf-8").read().splitlines()
    except OSError:
        return None
    for line in reversed(lines[-300:]):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("請求者") == agent and r.get("動作") == "拒絕" and sorted(r.get("項目", [])) == want:
            n_denied += 1
            if n_denied == 1:
                first = r["時間"]
    if not n_denied:
        return None
    t = time.mktime(time.strptime(first, "%Y-%m-%d %H:%M:%S"))
    return first if time.time() - t < (DENY_QUIET if n_denied == 1 else 7 * 24 * 3600) else None


def allowed_once_count(agent, items):
    """同一批項目被「允許一次」幾次（Gemini 14:47：3 次以上就提示改走放寬提案）。"""
    want, n = sorted(items), 0
    try:
        for line in open(PERM_LOG, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("請求者") == agent and r.get("動作") == "允許一次" and sorted(r.get("項目", [])) == want:
                n += 1
    except OSError:
        pass
    return n


def handle(who, text, grant):
    """群組每則發言都丟進來；回傳要貼的公告（沒事回 None）。grant(name, [dir], 由) 由中樞提供。"""
    t = (text or "").strip()
    m = re.match(r"放寬提案\s*[:：]\s*(\S+)\s*可寫\s*([^\s｜|]+)\s*(?:[｜|]\s*理由\s*[:：]?\s*(.*))?", t, re.S)
    if m:
        target, path, why = m.group(1), m.group(2).rstrip("，。；"), (m.group(3) or "").strip()[:300]
        # Codex 14:48：核可綁定確切路徑與範圍——提案建立後不能改，要改就重新提案（新編號、要重新核可）
        target = {"輝達": "輝達", "nvidia": "輝達", "deepseek": "DeepSeek", "gemini": "Gemini"}.get(target.lower(), target)
        bad = red_line(path, target)
        if not bad:
            path = os.path.realpath(path)
        if bad:
            return f"🔐 放寬提案不受理：{target} 可寫 {path}——{bad}。這類只能請使用者決定。"
        d = _load()
        n = d["next"]
        d["next"] += 1
        d["items"][str(n)] = {"提案人": who, "對象": target, "資料夾": path, "理由": why, "時間": time.strftime("%Y-%m-%d %H:%M"),
                              "狀態": "待審"}
        _save(d)
        return (f"🔐 放寬提案 #{n}（{who} 提）：{target} 以後可寫 {path}。理由：{why or '（沒寫）'}\n"
                f"請 {who if who == target else who + '、' + target} 以外的 AI 審核：行首寫「核可放寬 #{n}」或「否決放寬 #{n} 理由」。")
    m = re.match(r"(核可|否決)放寬\s*#?(\d+)\s*(.*)", t, re.S)
    if not m:
        return None
    ok, n, note = m.group(1) == "核可", m.group(2), m.group(3).strip()[:200]
    d = _load()
    it = d["items"].get(n)
    if not it or it["狀態"] != "待審":
        return f"🔐 找不到待審的放寬提案 #{n}。"
    if who not in AIS:
        return f"🔐 {who} 不能審核放寬提案（只有本機的 Claude／Codex／Gemini／輝達／DeepSeek 可以；使用者要直接決定就用一般的允許按鈕）。"
    if who in (it["提案人"], it["對象"]):
        return f"🔐 #{n} 要由提案人、受益人以外的 AI 審核（{who} 不行）。"
    it.update({"狀態": "通過" if ok else "否決", "審核": who, "審核意見": note, "審核時間": time.strftime("%Y-%m-%d %H:%M")})
    _save(d)
    with open(PERM_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"時間": time.strftime("%Y-%m-%d %H:%M:%S"), "請求者": it["對象"], "項目": [f"寫入資料夾 {it['資料夾']}"],
                            "動作": "以後都允許（AI 審核）" if ok else "放寬提案否決", "批准者": who, "提案人": it["提案人"],
                            "來源": f"放寬提案 #{n}"}, ensure_ascii=False) + "\n")
    if not ok:
        return f"🔐 放寬提案 #{n} 被 {who} 否決：{note or '（沒寫理由）'}"
    grant(it["對象"], [it["資料夾"]], f"放寬提案 #{n}，{it['提案人']} 提、{who} 核可")
    extra = ("\n@Claude 對象住視窗，要另外改它視窗的允許設定（agy／OpenCode），請照這張提案改。"
             if it["對象"] in ("Gemini", "輝達") else "")
    return (f"🔐 放寬提案 #{n} 通過（{who} 核可）：{it['對象']} 以後可寫 {it['資料夾']}。"
            f"使用者可在權限總覽看到，要撤銷跟 Claude 說。{extra}")
