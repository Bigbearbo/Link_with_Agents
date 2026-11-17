"""搜尋三方討論的對話紀錄（logs\*.md，每個房間一份，中樞自動寫）。給人和 AI 用，不呼叫任何模型。

  python search.py 關鍵字 [關鍵字…]            每個關鍵字都要出現（不分大小寫）
  python search.py 加法 進位 --who 使用者        只看某人說的（使用者／Claude／Codex／系統…，部分符合即可）
  python search.py 雙腦 --room 主討論 --since 11-17   限定房間、日期（MM-DD 起）
  python search.py "R\\d+" --re                 用正規表示式
  python search.py 雙腦 --full                   印出整則，不只片段
  python search.py --last 20 --room 主討論        不搜尋，只看最近 20 則

片段預設前後 120 字、最多 20 筆（--context、--limit 調整），最新的在最後。
"""
import argparse, glob, os, re, sys

LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
HEAD = re.compile(r"^## (.+?)（(.*?)）\s*(.*)$")
ROOM_NAMES = {"tg-": "主討論"}  # 舊檔名對照


def room_of(fn):
    base = os.path.basename(fn)[:-3]
    for k, v in ROOM_NAMES.items():
        if base.startswith(k):
            return v
    return re.sub(r"-\d{8}-\d{6}$", "", base) or base


def entries():
    out = []
    for fn in sorted(glob.glob(os.path.join(LOGS, "*.md"))):
        room, cur = room_of(fn), None
        for line in open(fn, encoding="utf-8", errors="replace"):
            m = HEAD.match(line.rstrip("\n"))
            if m:
                cur = dict(room=room, who=m.group(1), src=m.group(2), time=m.group(3).strip(), text=[])
                out.append(cur)
            elif cur is not None:
                cur["text"].append(line)
    for e in out:
        e["text"] = "".join(e["text"]).strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words", nargs="*")
    ap.add_argument("--who"); ap.add_argument("--room"); ap.add_argument("--since")
    ap.add_argument("--re", action="store_true"); ap.add_argument("--full", action="store_true")
    ap.add_argument("--context", type=int, default=120); ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--last", type=int)
    a = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    es = entries()
    if a.room:
        es = [e for e in es if a.room in e["room"]]
    if a.who:
        es = [e for e in es if a.who.lower() in e["who"].lower()]
    if a.since:
        es = [e for e in es if e["time"][:5] >= a.since]
    if a.last:
        es, pats = es[-a.last:], []
    else:
        if not a.words:
            ap.error("請給關鍵字，或用 --last N")
        pats = [re.compile(w if a.re else re.escape(w), re.I) for w in a.words]
        es = [e for e in es if all(p.search(e["text"]) for p in pats)]
    total = len(es)
    es = es[-a.limit:] if not a.last else es
    for e in es:
        t = e["text"]
        if not a.full and pats:
            m = pats[0].search(t)
            s, end = max(0, m.start() - a.context), min(len(t), m.end() + a.context)
            t = ("…" if s else "") + t[s:end].replace("\n", " ") + ("…" if end < len(t) else "")
        elif not a.full:
            t = t[:a.context * 2].replace("\n", " ") + ("…" if len(t) > a.context * 2 else "")
        print(f"【{e['room']}｜{e['time']}｜{e['who']}】{t}\n")
    if total > len(es):
        print(f"（共 {total} 筆，只列最新 {len(es)} 筆；用 --limit 或加關鍵字縮小）")
    elif not es:
        print("（沒有找到）")


if __name__ == "__main__":
    main()
