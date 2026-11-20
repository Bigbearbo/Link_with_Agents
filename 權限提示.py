"""Claude Code 的 Notification hook：終端機 Claude 停在權限確認框時，貼一則到三方討論主討論。
使用者 11-20 06:41：「CLI 有權限需求的時候能傳送或通知到主討論這裡嗎? 我比較不會漏掉」。
stdin 是 hook 的 JSON（含 message）；只轉權限類的通知，其他（閒置提醒）不轉。不印金鑰。
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    d = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
except ValueError:
    d = {}
who = sys.argv[1] if len(sys.argv) > 1 else "終端機 Claude"   # Codex 的 PermissionRequest 掛勾可用：python 權限提示.py "桌面 Codex"
msg = str(d.get("message") or "")
if not msg and d.get("tool_name"):
    msg = f"{d.get('tool_name')} {json.dumps(d.get('tool_input', ''), ensure_ascii=False)[:160]}"
if who == "終端機 Claude" and d.get("notification_type") not in (None, "permission_prompt"):
    sys.exit(0)
try:
    key = open(os.path.join(r"H:\金鑰", "web-key.txt"), encoding="utf-8").read().strip()
    body = json.dumps({"room": "main", "text": f"🔐 {who} 停在權限確認框，等你到那個視窗按：{msg[:200]}"}).encode()
    req = urllib.request.Request("http://127.0.0.1:8765/api/claude_says", data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8", "X-Key": key})
    urllib.request.urlopen(req, timeout=5).read()
except Exception:
    pass
