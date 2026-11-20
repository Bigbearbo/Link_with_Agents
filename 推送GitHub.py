r"""把三方討論中樞（聊天程式）推上 GitHub（使用者 11-20 08:36 回待討論 #31：「同意：可以隨時推，方便版本維護，或是定期推，有效率即可」）。
推之前一定先掃：這次要推的所有提交裡，新增的每一行都過一遍金鑰／個資規則；有任何一條命中就不推、回報命中位置（不印內容）。
從不 force push；遠端比本機新（有分歧）就不推、回報。
  python 推送GitHub.py          掃＋推
  python 推送GitHub.py 只掃     只掃不推
hub 每小時呼叫 periodic()：距上次推送超過 6 小時、且有新提交才推。
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "github推送.json")
RULES = [
    ("金鑰", re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{10,}|gsk_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_\-]{30,}|nvapi-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9\-]{10,})")),
    ("Telegram token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")),
    ("網頁金鑰參數", re.compile(r"[?&]k=[A-Za-z0-9_\-]{16,}")),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@(?!anthropic\.com|local\b|example\.)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("電話", re.compile(r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)")),
    ("學生成績", re.compile(r"座號\s*\d{1,2}\D{0,8}\d{2,3}\s*分")),   # 抓真的資料（座號＋分數），不抓「學生資料」這種規則字眼
]
EXEMPT = ("隱私篩.py", "推送GitHub.py")   # 這兩支本身就寫著偵測規則和假的測試金鑰


def git(*a, check=True):
    r = subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}：{(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def scan(base="origin/main"):
    hits = []
    files = [f for f in git("diff", "--name-only", f"{base}..HEAD").splitlines() if f]
    bad_paths = [f for f in files if re.match(r"(secrets/|logs/|tg_state|state.*\.json|.*\.dpapi$|terminal/)", f)]
    for f in bad_paths:
        hits.append((f, 0, "不該進公開倉庫的路徑"))
    diff = git("diff", "-U0", f"{base}..HEAD")
    cur, ln = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) - 1 if m else 0
        elif line.startswith("+") and cur:
            ln += 1
            if os.path.basename(cur) in EXEMPT:
                continue
            for name, rx in RULES:
                if rx.search(line[1:]):
                    hits.append((cur, ln, name))
    return files, hits


def run(push=True):
    git("fetch", "origin", "--quiet")
    behind = int(git("rev-list", "--count", "HEAD..origin/main").strip() or 0)
    ahead = int(git("rev-list", "--count", "origin/main..HEAD").strip() or 0)
    if ahead == 0:
        return "沒有新提交，不用推。"
    if behind:
        return f"遠端比本機多 {behind} 個提交（分歧），不推；請 Claude 手動處理（不會 force push）。"
    files, hits = scan()
    if hits:
        where = "；".join(f"{f}:{n}（{k}）" for f, n, k in hits[:10])
        return f"⛔ 掃到 {len(hits)} 處可能的敏感內容，不推：{where}"
    if not push:
        return f"掃過 {ahead} 個提交、{len(files)} 個檔，沒有命中；（只掃模式，沒推）"
    git("push", "origin", "HEAD:main")
    json.dump({"last": time.time(), "commits": ahead}, open(STATE, "w", encoding="utf-8"))
    return f"✅ 已推上 GitHub：{ahead} 個提交、{len(files)} 個檔，推前掃描 0 命中。"


def periodic(hub=None, every_h=6):
    try:
        last = json.load(open(STATE, encoding="utf-8")).get("last", 0)
    except (OSError, ValueError):
        last = 0
    if time.time() - last < every_h * 3600:
        return None
    msg = run(True)
    if msg.startswith("⛔") or msg.startswith("遠端") or msg.startswith("✅"):
        room = hub and hub.rooms.get("g1004308131170")
        if room:
            room.notice("GitHub 推送：" + msg + ("" if msg.startswith("✅") else "\n@Claude 請處理。"))
    return msg


if __name__ == "__main__":
    print(run(push=not (len(sys.argv) > 1 and sys.argv[1] == "只掃")))
