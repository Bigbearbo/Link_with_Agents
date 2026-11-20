"""終端機的 Claude 主動在討論房發言。

用法：python say.py <房間名稱或id> <文字檔路徑> [--then codex|claude|gemini|both]
（文字放檔案裡，避免命令列的中文和換行問題）
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
key = open(os.path.join(r"H:\金鑰", "web-key.txt"), encoding="utf-8").read().strip()


def api(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8765" + path, headers={"X-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


room_arg, text_file = sys.argv[1], sys.argv[2]
then = sys.argv[sys.argv.index("--then") + 1] if "--then" in sys.argv else None
rooms = api("/api/poll?room=main&since=999999&epoch=0")["rooms"]
match = [r for r in rooms if r["id"] == room_arg or r["name"] == room_arg or room_arg in r["name"]]
if len(match) != 1:
    sys.exit(f"找不到唯一的房間：{room_arg}（現有：{[r['name'] for r in rooms]}）")
text = open(text_file, encoding="utf-8").read().strip()
print(api("/api/claude_says", {"room": match[0]["id"], "text": text, "then": then}), "→", match[0]["name"])
