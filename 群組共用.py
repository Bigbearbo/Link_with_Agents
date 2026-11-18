r"""摘要：視窗 agent（Gemini、輝達）的群組工具本體——只有這一份；各家 `群組\群組.py` 只是三行轉呼叫，自帶說明文字。
更新：2026-11-19｜作者：Claude（使用者 06:58：「一些程式變成兩個，但來源只有一個，常常忘了改第二個」→ 單一真實來源）
放在中樞資料夾：agent 只能讀不能改，也不在雲端雙向同步範圍。
金鑰在各家家目錄 .群組金鑰.txt（低權限：只能讀房間、以自己名義發言／更新狀態）；「看到哪」記在各家 群組\看到哪.json。
"""
import hashlib, json, os, socket, sys, urllib.error, urllib.request

URL = "http://127.0.0.1:8765"


class Group:
    def __init__(self, here):
        self.here = here                                            # 各家的 群組\ 資料夾
        self.key = open(os.path.join(os.path.dirname(here), ".群組金鑰.txt"), encoding="utf-8").read().strip()
        self.seen = os.path.join(here, "看到哪.json")

    def api(self, path, body=None):
        r = urllib.request.Request(URL + path, headers={"X-Agent-Key": self.key, "Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        return json.loads(urllib.request.urlopen(r, timeout=15).read())

    def rooms(self):
        return self.api("/api/poll?room=main&since=999999&epoch=0")["rooms"]

    def find(self, arg):
        rs = self.rooms()
        m = [r for r in rs if r["id"] == arg] or [r for r in rs if arg in r["name"] or arg in r["id"]]
        if len(m) != 1:
            raise SystemExit(f"找不到唯一的房間「{arg}」：" + "、".join(f"{r['name']}({r['id']})" for r in rs))
        return m[0]

    def show(self, r, n, only_new):
        seen = {}
        try:
            seen = json.load(open(self.seen, encoding="utf-8"))
        except (OSError, ValueError):
            pass
        d = self.api(f"/api/poll?room={r['id']}&since=0&epoch=0")
        msgs, total, start = d["messages"], d["total"], d["start"]
        last = seen.get(r["id"]) if only_new else None
        msgs = msgs[max(0, last - start):] if last is not None else msgs[-n:]
        print(f"== {r['name']}（{r['id']}）共 {total} 則，這次 {len(msgs)} 則" + ("（新訊息）" if last is not None else ""))
        for m in msgs:
            print(f"\n【{m['who']}｜{m.get('time', '')}】{m['text']}")
        seen[r["id"]] = total
        json.dump(seen, open(self.seen, "w", encoding="utf-8"), ensure_ascii=False)


def main(here, doc):
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    g = Group(here)
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "房間":
        for r in g.rooms():
            print(f"{r['id']}\t{r['name']}\t{r['total']} 則" + ("\t（有人在回答）" if r["busy"] else ""))
    elif cmd in ("看", "看全部"):
        g.show(g.find(a[1] if len(a) > 1 else "main"), int(a[2]) if len(a) > 2 else 15, cmd == "看")
    elif cmd == "狀態" and len(a) >= 3:
        r = g.find(a[1])
        body = {"room": r["id"], "done": True} if a[2] == "結束" else {"room": r["id"], "text": " ".join(a[3:] if a[2] == "--" else a[2:])}
        print("已更新狀態" if g.api("/api/agent_status", body).get("ok") else "狀態更新失敗")
    elif cmd == "說" and len(a) >= 3:
        r = g.find(a[1])
        text = (" ".join(a[3:]) if a[2] == "--" else open(a[2], encoding="utf-8").read()).strip()
        if not text:
            raise SystemExit("沒有內容")
        # 同房間＋同一段文字＝同一個 client_id；中樞記得最近 200 個，逾時後重送同一段不會重複貼
        cid = hashlib.sha1(f"{r['id']}|{text}".encode()).hexdigest()[:24]
        try:
            res = g.api("/api/agent_says", {"room": r["id"], "text": text, "client_id": cid})
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            raise SystemExit(f"送出狀態未知（{type(e).__name__}，識別碼 {cid}）：重送『同一段文字』是安全的，中樞會用識別碼去重；改了內容再送就算新的一則")
        print("已貼出（重複，略過）" if res.get("duplicate") else "已貼出" if res.get("ok") else "貼出失敗")
    else:
        print(doc)
