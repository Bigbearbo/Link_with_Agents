"""摘要：查共用區變動紀錄（使用者 11-19 09:06）。本體在 shared_watch.query_changes，網頁「變動」頁用同一個函式。
用法：PY 變動查詢.py [關鍵字] [--since 11-19] [--until "11-19 12:00"] [--新|--改] [--n 50]
例：PY 變動查詢.py deepseek --since 11-19
"""
import sys

import shared_watch as sw

a = sys.argv[1:]
opt = {"since": "", "until": "", "kind": "", "n": 50}
q = []
i = 0
while i < len(a):
    if a[i] in ("--since", "--until", "--n") and i + 1 < len(a):
        opt[a[i][2:]] = a[i + 1]
        i += 2
        continue
    if a[i] in ("--新", "--改"):
        opt["kind"] = a[i][2:]
    else:
        q.append(a[i])
    i += 1
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
rows = sw.query_changes(" ".join(q), opt["since"], opt["until"], opt["kind"], int(opt["n"]))
for r in rows:
    print(f"{r['時間']} {r['類']} {r['路徑']}" + (f"（{r['負責']}）" if r.get("負責") else "") + (f"｜{r['摘要']}" if r.get("摘要") else ""))
print(f"— 共 {len(rows)} 筆" + ("（只列最新的 %s 筆）" % opt["n"] if len(rows) >= int(opt["n"]) else ""))
