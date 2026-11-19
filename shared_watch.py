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
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (root == SHARED and d == "inbox")]
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
                now[rel] = old
                continue
            try:
                sha = _sha(full)
            except OSError:
                continue
            now[rel] = [mt, sha]
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
    for name in sorted(os.listdir(INBOX)):
        full = os.path.join(INBOX, name)
        if not os.path.isfile(full) or name.endswith(SKIP_EXT) or name.startswith("."):
            continue
        if time.time() - os.path.getmtime(full) < 3:
            continue  # 可能還在寫
        try:
            text = open(full, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        head, sep, body = text.partition("\n---")
        meta = {}
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        sender = (meta.get("from") or "").strip()[:20]
        to = (meta.get("to") or "none").strip().lower()
        if not sep or not sender or to not in ("claude", "codex", "gemini", "nvidia", "both", "none"):
            out.append((full, sender or "未知", "invalid", text[:300], None))
        else:
            out.append((full, sender, to, body.lstrip("-\n").strip(), (meta.get("room") or "").strip() or None))
    return out


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
_rules_cache = {"mtime": None, "rules": None}


def rules():
    """回傳 {"skip": [樣式…], "cooldown": 秒, "batch": 秒}；檔案壞掉或不存在就用預設。"""
    default = {"skip": [], "cooldown": 3600, "batch": 120}
    try:
        mt = os.path.getmtime(RULES_FILE)
    except OSError:
        return default
    if _rules_cache["mtime"] == mt:
        return _rules_cache["rules"]
    r = dict(default, skip=[])
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
    return (f"共用區有變動：新 {len(added)}、改 {len(changed)}，主要在 {main}。"
            "細節用網頁「變動」頁查（可篩誰、關鍵字、時間），或 `PY H:\Claude用\三方討論\變動查詢.py 關鍵字 --since 11-19`。")


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
