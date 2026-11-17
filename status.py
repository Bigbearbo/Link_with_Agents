"""快速看中樞狀態：各房間誰在工作、做到哪、最後幾則訊息。用法：python status.py [則數]"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
k = open(os.path.join(HERE, "secrets", "web-key.txt"), encoding="utf-8").read().strip()


def poll(room, since=0):
    req = urllib.request.Request(f"http://127.0.0.1:8765/api/poll?room={room}&since={since}&epoch=0",
                                 headers={"X-Key": k})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


first = poll("main", 10 ** 6)
print("終端機在線：", first.get("terminal"), "｜自我檢查：",
      {a: v["ok"] for a, v in (first.get("health") or {}).items()})
for r in first["rooms"]:
    d = poll(r["id"])
    print(f"\n== {r['name']}（{d['total']} 則）{d['status']}")
    for p in d["progress"]:
        print(f"  {p['who']} 已 {p['elapsed']} 秒：", " / ".join(p["steps"][-3:]))
    for m in d["messages"][-n:]:
        print(f"  [{m['who']}|{m.get('src', '')}|{m['time']}] {m['text'][:160].replace(chr(10), ' ')}")


# 今天的用量（中樞呼叫的備援 Claude 與 Codex；終端機的 Claude 請主人在終端機打 /usage）
try:
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    rows = [json.loads(l) for l in open(os.path.join(HERE, "usage.jsonl"), encoding="utf-8") if l.startswith("{")]
    rows = [r for r in rows if r.get("time", "").startswith(today)]
    for who in ("Claude", "Codex"):
        mine = [r for r in rows if r["agent"] == who]
        if not mine:
            continue
        cost = sum(r.get("cost_usd") or 0 for r in mine)
        tin = sum(r.get("input_tokens") or 0 for r in mine)
        tout = sum(r.get("output_tokens") or 0 for r in mine)
        extra = f"，花費約 {cost:.2f} 美元" if who == "Claude" else ""
        print(f"\n今天 {who}：{len(mine)} 次呼叫，輸入 {tin:,} token、輸出 {tout:,} token{extra}")
except FileNotFoundError:
    print("\n（還沒有用量紀錄）")
