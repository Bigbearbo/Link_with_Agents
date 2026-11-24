"""監看 H:\\Agent共用：有新檔／改檔就在互相討論群貼一行；inbox/ 的檔案當成 App AI 的發言。

不呼叫任何模型；只用 mtime＋sha256 判斷內容真的變了。
inbox 檔案格式（UTF-8，先寫 .tmp 再改名）：
    from: 視窗Claude
    to: claude | codex | gemini | both | none
    ---
    內容……
"""
import hashlib
import json
import os
import shutil
import time

SHARED = r"H:\Agent共用"
INBOX = os.path.join(SHARED, "inbox")
SKIP_DIRS = {"__pycache__", "done", ".git", ".catalog", "jobs"}  # jobs：外部 AI 工作資料夾，結果另有通知
SKIP_EXT = (".tmp", ".pyc", ".lock", ".log", ".exe", ".dll", ".node", ".bin")
SKIP_NAMES = {"INDEX.json", "usage.jsonl", "claude-用量.json"}  # 自動產生、每次呼叫都會變的檔


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def owner_of(path):
    """往上找最近的 OWNER.txt，取第一行「負責人：」後面的名字。"""
    d = os.path.dirname(path)
    while d.startswith(SHARED) and len(d) >= len(SHARED):
        o = os.path.join(d, "OWNER.txt")
        if os.path.exists(o):
            try:
                first = open(o, encoding="utf-8-sig", errors="replace").readline().strip()
                return first.split("：", 1)[-1].split("（")[0].strip()[:30]
            except Exception:
                return ""
        if d == SHARED:
            break
        d = os.path.dirname(d)
    return ""


def scan(prev):
    """回傳 (新的快照, 新增清單, 修改清單)。prev 是 {相對路徑: [mtime, sha]}。"""
    now, added, changed = {}, [], []
    for root, dirs, files in os.walk(SHARED):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".git") and not (root == SHARED and d == "inbox")]  # 11-21 02:48 誤報：GPT報告\_repo.git 內部檔
        for name in files:
            if name.endswith(SKIP_EXT) or name in SKIP_NAMES:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, SHARED).replace("\\", "/")
            try:
                mt = os.path.getmtime(full)
            except OSError:
                continue
            old = prev.get(rel)
            if old and abs(old[0] - mt) < 1e-6:
                if len(old) < 3:   # 舊快照沒有大小：補上（告警要比「縮小」）
                    try:
                        old = list(old[:2]) + [os.path.getsize(full)]
                    except OSError:
                        pass
                now[rel] = old
                continue
            try:
                sha = _sha(full)
                size = os.path.getsize(full)
            except OSError:
                continue
            now[rel] = [mt, sha, size]
            if not old:
                added.append(rel)
            elif old[1] != sha:
                changed.append(rel)
    return now, added, changed


def summarize(added, changed, limit=8):
    """把變動整理成一段短通知；同一資料夾太多檔就合併成一行。"""
    lines = []
    for mark, items in (("📄 新檔", added), ("🆕 更新", changed)):
        by_dir = {}
        for rel in items:
            by_dir.setdefault(os.path.dirname(rel) or ".", []).append(rel)
        for d, rels in sorted(by_dir.items()):
            if len(rels) > 3:
                who = owner_of(os.path.join(SHARED, rels[0]))
                lines.append(f"{mark}：{d}/ 共 {len(rels)} 個檔" + (f"（{who}）" if who else ""))
            else:
                for rel in rels:
                    who = owner_of(os.path.join(SHARED, rel))
                    lines.append(f"{mark}：{rel}" + (f"（{who}）" if who else ""))
    if len(lines) > limit:
        lines = lines[:limit] + [f"……另外還有 {len(lines) - limit} 項"]
    return "\n".join(lines)


def read_inbox():
    """回傳 [(檔案路徑, from, to, 內容)]；格式不對的檔回傳 to='invalid'。"""
    out = []
    if not os.path.isdir(INBOX):
        return out
    # 使用者 11-19 14:22：「不同系統寫入的確該分資料夾」→ inbox\<來源>\（例 inbox\Dot\、inbox\視窗Gemini\）也收，資料夾名當預設寄件人
    spots = [(INBOX, None)] + [(os.path.join(INBOX, d), d) for d in sorted(os.listdir(INBOX))
                               if os.path.isdir(os.path.join(INBOX, d)) and d not in ("done", "rejected") and not d.startswith(".")]
    for folder, who in spots:
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if not os.path.isfile(full) or name.endswith(SKIP_EXT) or name.startswith(".") or name.lower() in ("readme.md", "_說明.txt"):
                continue
            if time.time() - os.path.getmtime(full) < 3:
                continue  # 可能還在寫
            try:
                raw = open(full, "rb").read()
            except OSError:
                continue
            sender, to, body, room = parse_ticket(name, raw)
            if who and sender in ("未知", "", "雲端 App"):
                sender = who[:20]
            elif sender == "未知" and to not in ("invalid", "gdoc"):
                to = "unclear"
            if to not in ("invalid", "gdoc") and body.count("�") >= 5:   # 亂碼：文字還是轉，但交給輝達先看
                body = "（這張單有亂碼，可能是不同系統的編碼問題；看得懂的部分照辦，看不懂的請回問寄件人）\n" + body
                to = "unclear"
            out.append((full, sender, to, body, room))
    return out


# 使用者 11-19 14:16：「蘋果＋谷歌＋Windows，一些符號就轉不過去，把判斷機制放寬……有異常的可以轉單呼叫小 AI 如輝達進行分派和程式修補」
TO_ALIAS = {"claude": "claude", "克勞德": "claude", "codex": "codex", "gemini": "gemini", "雙子": "gemini",
            "nvidia": "nvidia", "輝達": "nvidia", "deepseek": "deepseek", "深度求索": "deepseek",
            "both": "both", "all": "both", "全部": "both", "大家": "both", "所有人": "both",
            "none": "none", "無": "none", "不用": "none", "dot": "dot"}
KEY_ALIAS = {"from": "from", "寄件": "from", "寄件人": "from", "來自": "from", "發件": "from", "發件人": "from", "誰": "from",
             "to": "to", "給": "to", "收件": "to", "收件人": "to", "交給": "to", "派給": "to",
             "room": "room", "房間": "room", "房": "room"}
FIX_HINT = "（解析器放寬後仍讀不懂的地方，輝達可在 H:\\免費模型用\\work\\inbox解析建議.md 寫修法，Claude 看了再改）"


def clean_text(raw):
    """各平台的怪符號先統一：BOM、CRLF、全形冒號／破折號、零寬字元、不換行空白、智慧引號。"""
    for enc in ("utf-8-sig", "utf-16", "cp950"):
        try:
            t = raw.decode(enc)
            if "\x00" not in t:
                break
        except UnicodeDecodeError:
            continue
    else:
        t = raw.decode("utf-8", "replace")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    for a in ("​", "‌", "‍", "﻿"):   # 只拿掉看不見的字元；內文的全形標點保留（21:4x：曾把發言裡的全形冒號改成半形）
        t = t.replace(a, "")
    return t


def punct(line):
    """只用在讀檔頭欄位時：全形冒號、智慧引號、不換行空白統一。"""
    for a, b in ((" ", " "), ("　", " "), ("：", ":"), ("﹕", ":"), ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        line = line.replace(a, b)
    return line


def parse_ticket(name, raw):
    """回傳 (from, to, 內容, room)。to 可能是 'gdoc'（Google 文件捷徑，本機讀不到內容）或 'unclear'（讀得到但不知道給誰）。"""
    import re
    if name.lower().endswith((".gdoc", ".gsheet", ".gslides")):
        try:
            doc = json.loads(raw.decode("utf-8", "replace")).get("doc_id", "")
        except ValueError:
            doc = ""
        return ("Dot" if "DOT_" in name.upper() else "雲端 App", "gdoc", doc, None)
    text = clean_text(raw)
    lines = text.split("\n")
    meta, body_start = {}, 0
    for i, line in enumerate(lines[:15]):                       # 檔頭只看前 15 行
        if re.fullmatch(r"[-—－─=_*]{3,}", line.replace(" ", "")) and meta:
            body_start = i + 1
            break
        s = re.sub(r"^[\s>#*\-_`]*|[*_`]+(?=\s*:)", "", punct(line)).strip()   # \u5bb9\u5fcd markdown\uff1a**from**: \uff0f - to: \uff0f # from:
        m = re.match(r"([A-Za-z\u4e00-\u9fff]{1,6})\s*[:=]\s*(.*)", s)
        if m and KEY_ALIAS.get(m.group(1).lower()):
            meta[KEY_ALIAS[m.group(1).lower()]] = m.group(2).strip().strip('"\'')
            body_start = i + 1
        elif s and meta:                                        # 檔頭後面沒有 --- 也接受：第一行不像欄位的就是內文
            break
    body = "\n".join(lines[body_start:]).strip() if meta else text.strip()
    sender = (meta.get("from") or ("Dot" if name.upper().startswith("DOT_") else "")).strip()[:20]
    want = (meta.get("to") or "").lower()
    to = next((v for k, v in TO_ALIAS.items() if k in want), None) if want else None
    if to is None and name.upper().startswith("DOT_REQUEST"):
        to = "claude"                                           # 使用說明第 3 節的 DOT_REQUEST 四欄格式：給本機（Claude 先接）
    if not body:
        return (sender or "未知", "invalid", text[:300], None)
    if to is None:
        return (sender or "未知", "unclear", body, meta.get("room") or None)
    if not sender:
        return ("未知", to, body, meta.get("room") or None)   # 有寫給誰、沒寫是誰：read_inbox 用資料夾名補；補不到才轉輝達
    return (sender, to, body, meta.get("room") or None)


def archive(path, ok=True):
    dest = os.path.join(INBOX, "done" if ok else "rejected")
    os.makedirs(dest, exist_ok=True)
    shutil.move(path, os.path.join(dest, time.strftime("%m%d-%H%M%S-") + os.path.basename(path)))


def load_seen(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def save_seen(path, snap):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------- 通知規則（H:\Agent共用\通知規則.txt，大家都能改；每輪重讀） ----------
import fnmatch

RULES_FILE = os.path.join(SHARED, "通知規則.txt")
# 敏感位置：被刪或一次縮小一半以上就立即告警（Codex 11-20 07:43）。通知規則.txt 的「敏感: 樣式」可再加。
SENSITIVE_DEFAULT = ("通知規則.txt", "使用者備忘.md", "誤會更正.md", "AI自決紀錄.md", "待辦/待辦.json", "連動清單.md",
                     "團隊目標.md", "權限總覽.md", "OWNER.txt")
_rules_cache = {"mtime": None, "rules": None}


def rules():
    """回傳 {"skip": [樣式…], "cooldown": 秒, "batch": 秒}；檔案壞掉或不存在就用預設。"""
    default = {"skip": [], "cooldown": 3600, "batch": 120, "sensitive": list(SENSITIVE_DEFAULT), "mass_delete": 10}
    try:
        mt = os.path.getmtime(RULES_FILE)
    except OSError:
        return default
    if _rules_cache["mtime"] == mt:
        return _rules_cache["rules"]
    r = dict(default, skip=[], sensitive=list(SENSITIVE_DEFAULT))
    try:
        for line in open(RULES_FILE, encoding="utf-8-sig", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.replace("：", ":").partition(":")
            k, v = k.strip(), v.strip()
            if k == "略過" and v:
                r["skip"].append(v)
            elif k == "冷卻分鐘":
                r["cooldown"] = max(0, float(v)) * 60
            elif k == "合併秒數":
                r["batch"] = max(0, float(v))
            elif k == "敏感" and v:          # 只能加，不能拿掉內建的（避免改規則把告警關掉）
                r["sensitive"].append(v)
            elif k == "大量刪檔":
                r["mass_delete"] = max(3, int(float(v)))
    except (OSError, ValueError):
        return default
    _rules_cache.update(mtime=mt, rules=r)
    return r


def skipped(rel, pats):
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(name, p) for p in pats)


class Throttle:
    """把掃描結果過濾（略過清單）、冷卻（同檔一小時一次）、合併（湊一批再發）。"""

    def __init__(self):
        self.pending = {}    # rel -> "added"/"changed"
        self.first_at = None
        self.last_sent = {}  # rel -> 上次通知時間
        self.cooling = {}    # rel -> 冷卻中又改了（等冷卻結束補報）

    def feed(self, added, changed):
        r, now = rules(), time.time()
        for rel in added:
            if not skipped(rel, r["skip"]):
                self.pending[rel] = "added"
        for rel in changed:
            if skipped(rel, r["skip"]) or self.pending.get(rel) == "added":
                continue
            if now - self.last_sent.get(rel, 0) < r["cooldown"]:
                self.cooling[rel] = True
            else:
                self.pending[rel] = "changed"
        for rel in [k for k in self.cooling if now - self.last_sent.get(k, 0) >= r["cooldown"]]:
            del self.cooling[rel]
            self.pending.setdefault(rel, "changed")
        if self.pending and self.first_at is None:
            self.first_at = now

    def ready(self):
        """湊滿合併時間就回傳 (added, changed) 並清空；否則回傳 None。"""
        if not self.pending or time.time() - self.first_at < rules()["batch"]:
            return None
        now = time.time()
        added = sorted(k for k, v in self.pending.items() if v == "added")
        changed = sorted(k for k, v in self.pending.items() if v == "changed")
        for k in self.pending:
            self.last_sent[k] = now
        self.pending, self.first_at = {}, None
        return added, changed


# ---------- 立即告警（使用者 11-20 07:42「檔案監看程式可以即時介入」；Codex 07:43「短時間大量刪檔或改到敏感位置才立即通知負責 AI 和 Claude」） ----------
ALARM_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "共用區告警.jsonl")


class Alarm:
    """不走合併／冷卻：10 分鐘內刪掉 ≥ N 個檔（通知規則「大量刪檔」，預設 10），或敏感檔被刪／一次縮小一半以上。
    feed() 回傳 [(負責人集合, 訊息)]。量測用：每次告警記一行到 logs\\共用區告警.jsonl。"""
    WINDOW = 600

    def __init__(self):
        self.recent = []      # [(時間, rel)] 最近刪掉的
        self.mass_until = 0   # 大量刪檔告警後，同一個窗口內不再重報

    def feed(self, prev, snap, now=None):
        now = now or time.time()
        r = rules()
        sens = lambda rel: skipped(rel, r["sensitive"])
        tmpish = lambda k: (".tmp" in k or "__pycache__" in k or "/~$" in k or k.endswith((".pyc", ".tmp", ".part", ".crdownload")))
        gone = [k for k in prev if k not in snap and not skipped(k, r["skip"]) and not tmpish(k)   # 11-20 09:58 誤報：雲端硬碟的 .tmp.driveupload 暫存檔
                and not os.path.exists(os.path.join(SHARED, k))]   # 讀檔失敗被掃漏的不算刪
        self.recent = [(t, k) for t, k in self.recent if not tmpish(k)]
        out = []
        self.recent = [(t, k) for t, k in self.recent if now - t < self.WINDOW] + [(now, k) for k in gone]
        if len(self.recent) >= r["mass_delete"] and now >= self.mass_until:
            rels = [k for _, k in self.recent]
            owners = {owner_of(os.path.join(SHARED, k)) for k in rels} - {""}
            top = {}
            for k in rels:
                top[k.split("/")[0]] = top.get(k.split("/")[0], 0) + 1
            where = "、".join(f"{k}（{n}）" for k, n in sorted(top.items(), key=lambda x: -x[1])[:4])
            out.append((owners, "大量刪檔", rels, f"10 分鐘內共用區刪掉 {len(rels)} 個檔，主要在 {where}。"))
            self.mass_until = now + self.WINDOW
        hits = [(k, "被刪除") for k in gone if sens(k)]
        for k, v in snap.items():
            old = prev.get(k)
            if old and len(old) > 2 and len(v) > 2 and old[2] >= 200 and v[2] < old[2] * 0.5 and sens(k):
                hits.append((k, f"一次縮小 {old[2]}→{v[2]} 位元組"))
        if hits:
            owners = {owner_of(os.path.join(SHARED, k)) for k, _ in hits} - {""}
            out.append((owners, "敏感檔", [k for k, _ in hits],
                        "敏感檔異動：" + "；".join(f"{k} {why}" for k, why in hits[:6]) + (f"……共 {len(hits)} 個" if len(hits) > 6 else "") + "。"))
        res = []
        for owners, kind, rels, text in out:
            try:
                os.makedirs(os.path.dirname(ALARM_LOG), exist_ok=True)
                with open(ALARM_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"時間": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "類": kind,
                                        "檔數": len(rels), "路徑": rels[:20], "負責": sorted(owners)}, ensure_ascii=False) + "\n")
            except OSError:
                pass
            others = sorted(owners - {"Claude"})
            tag = " ".join(f"@{o}" for o in others) + (" " if others else "")
            res.append((owners, f"🚨 {text}\n{tag}@Claude 請先停下相關工作、確認是不是故意的；不是就從 git 退回"
                                f"（`git -C H:\\Agent共用 log --stat -3`）。這是立即告警，不等合併。"))
        return res


# ---------- 變動紀錄與查詢（使用者 11-19 09:06：群組只放簡介，查詢可以加條件，例如跟 DeepSeek 相關、時間範圍） ----------
CHANGE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "共用區變動.jsonl")


def headline(path):
    """檔案前 5 行裡的「摘要：」，沒有就空字串。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(5), f):
                t = line.strip().lstrip("#\"' ").strip()
                if t.startswith("摘要："):
                    return t[3:][:100]
    except OSError:
        pass
    return ""


def log_changes(added, changed):
    """每一筆變動記一行：時間、新／改、路徑、負責人、摘要。"""
    os.makedirs(os.path.dirname(CHANGE_LOG), exist_ok=True)
    t = time.strftime("%Y-%m-%d %H:%M")
    with open(CHANGE_LOG, "a", encoding="utf-8") as f:
        for kind, items in (("新", added), ("改", changed)):
            for rel in items:
                full = os.path.join(SHARED, rel)
                f.write(json.dumps({"時間": t, "類": kind, "路徑": rel.replace("\\", "/"),
                                    "負責": owner_of(full), "摘要": headline(full)}, ensure_ascii=False) + "\n")


def brief(added, changed):
    """群組用的簡介：幾個檔、主要在哪幾個資料夾。"""
    top = {}
    for rel in list(added) + list(changed):
        k = rel.replace("\\", "/").split("/")[0]
        top[k] = top.get(k, 0) + 1
    main = "、".join(f"{k}（{n}）" for k, n in sorted(top.items(), key=lambda x: -x[1])[:4])
    out = (f"共用區有變動：新 {len(added)}、改 {len(changed)}，主要在 {main}。"
           "細節用網頁「變動」頁查（可篩誰、關鍵字、時間），或 `PY H:\Claude用\三方討論\變動查詢.py 關鍵字 --since 11-19`。")
    # 使用者 11-25 05:17：視窗 App 的 AI 寫的報告要顯示出來，才能主動收到他們的貢獻 → 新報告列標題
    reps = [r for r in added if is_report(r)][:4]
    for rel in reps:
        full = os.path.join(SHARED, rel)
        who = owner_of(full)
        out += f"\n📄 新報告：{rel.replace(chr(92), '/')}" + (f"（{who}）" if who else "") + (f"——{report_title(full)}" if report_title(full) else "")
    more = sum(1 for r in added if is_report(r)) - len(reps)
    if more > 0:
        out += f"\n（另有 {more} 份新報告，看「變動」頁）"
    return out


REPORT_DIRS = ("報告", "GPT報告", "SLE_NN_Research_Exchange", "Gemini", "DeepSeek", "交接", "研究想法庫")
REPORT_WORDS = ("報告", "REPORT", "RESULT", "回報", "結論", "交件", "回執", "研究觀察", "交接")


def is_report(rel):
    """新檔算不算「報告」：放在約好的報告區，或檔名有報告類字眼；只看 .md／.txt。"""
    r = rel.replace("\\", "/")
    if not r.lower().endswith((".md", ".txt")) or "/_" in r or r.split("/")[-1].startswith("_"):
        return False
    return r.split("/")[0] in REPORT_DIRS or any(w.lower() in r.split("/")[-1].lower() for w in REPORT_WORDS)


def report_title(path):
    """報告標題：「摘要：」優先，沒有就第一個 # 標題或第一行非空字。"""
    h = headline(path)
    if h:
        return h
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(8), f):
                t = line.strip().lstrip("#").strip()
                if t:
                    return t[:80]
    except OSError:
        pass
    return ""


def query_changes(q="", since="", until="", kind="", limit=200):
    """篩變動紀錄：q 比對路徑／負責人／摘要（不分大小寫）；since／until 寫 11-19、11-19 08:00 或 2026-11-19；kind＝新／改。新的在前。"""
    def norm(s, end=False):
        s = s.strip()
        if not s:
            return ""
        if len(s) <= 5:                          # 11-19
            s = time.strftime("%Y-") + s
        elif len(s) <= 11 and s[2] == "-":       # 11-19 08:00
            s = time.strftime("%Y-") + s
        return s + (" 99:99" if end and len(s) == 10 else "")
    since, until = norm(since), norm(until, True)
    out = []
    try:
        lines = open(CHANGE_LOG, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if since and r["時間"] < since or until and r["時間"] > until:
            continue
        if kind and r["類"] != kind:
            continue
        if q and q.lower() not in (r["路徑"] + r.get("負責", "") + r.get("摘要", "")).lower():
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def digest(rule, since):
    """訂閱摘要（使用者 11-19 09:08：「agent 也可以像人一樣訂閱資訊」）。rule＝{"關鍵字": [...], "類": "", "最多": 10}；
    關鍵字任一個符合路徑／負責人／摘要就列，空清單＝全部。回傳 markdown 條列（新的在前）。"""
    kws = rule.get("關鍵字") or [""]
    rows, seen = [], set()
    for kw in kws:
        for r in query_changes(kw, since, "", rule.get("類", ""), 50):
            if r["路徑"] not in seen:
                seen.add(r["路徑"])
                rows.append(r)
    rows = sorted(rows, key=lambda r: r["時間"], reverse=True)[:int(rule.get("最多", 10))]
    return "\n".join(f"- {r['時間'][5:]} {r['類']} {r['路徑']}" + (f"｜{r['摘要']}" if r.get("摘要") else "") for r in rows)
