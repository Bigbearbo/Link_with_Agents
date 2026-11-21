r"""權限逾時自動拒絕（使用者 11-21 08:57：「我不在電腦前的時候，輝達權限沒反應 30 分鐘，自己找其他事做或找你們看怎麼處理」）。

規則（只做「拒絕」，絕不替使用者按允許）：
- 輝達 OpenCode 有權限請求等超過 30 分鐘，**而且使用者不在電腦前**（使用者電腦狀態：勾了不用電腦、或本機閒置 ≥ 30 分鐘、或寫「可能暫時不用」），
  → 用 OpenCode 的 /permission/<id>/reply 回 reject，並附一句話給輝達（模型看得到）：改做別的待辦，或到互相討論群請 Claude／Codex 代辦。
- 在互相討論群丟一則通知（inbox 單），使用者回來看得到發生過什麼。
- 使用者在電腦前時不動，照舊等使用者按。
中樞每 10 分鐘呼叫 tick()（掛在 資料夾入口.write，不改 hub.py）。
"""
import base64, json, os, re, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "權限逾時狀態.json")
PC = r"H:\Agent共用\使用者電腦狀態.md"
WAIT_SEC = 30 * 60
MSG = ("使用者目前不在電腦前，這個權限請求等了 30 分鐘沒人按，中樞已自動拒絕（不是使用者拒絕）。"
       "請不要重送同一個請求：先做其他不受影響的待辦，或在互相討論群行首 @Claude（中樞／一般檔案）或 @Codex（H:\\Codex用 的檔案），"
       "說明你要什麼檔或指令、做什麼用，請他們給副本或代跑。使用者回來後如果真的需要，再請他授權一次。")


def user_away():
    try:
        t = open(PC, encoding="utf-8").read()
    except OSError:
        return False
    if "不用電腦" in t or "可能暫時不用" in t:
        return True
    m = re.search(r"本機閒置[:：]\s*(\d+)\s*分鐘", t)
    return bool(m and int(m.group(1)) >= 30)


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tick(name="輝達"):
    import window_agent as wa
    pend = wa.pending_permissions(name)
    if pend is None:
        return []
    st = _load()
    now = time.time()
    ids = set()
    out = []
    for p in pend:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        ids.add(pid)
        first = st.setdefault(pid, {"first": now, "rejected": False})
        if first["rejected"] or now - first["first"] < WAIT_SEC or not user_away():
            continue
        try:
            c = json.load(open(wa.NOTIFY[name], encoding="utf-8"))
            auth = "Basic " + base64.b64encode(f"opencode:{c['password']}".encode()).decode()
            req = urllib.request.Request(f"http://127.0.0.1:{c['port']}/permission/{pid}/reply",
                                         data=json.dumps({"reply": "reject", "message": MSG}).encode("utf-8"),
                                         headers={"Authorization": auth, "Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            first["rejected"] = True
            what = p.get("title") or p.get("permission") or p.get("type") or ""
            pat = p.get("pattern") or p.get("patterns") or (p.get("metadata") or {}).get("filepath") or ""
            out.append(f"⏰ 權限逾時：{name} 的「{what} {pat}」等了 30 分鐘、使用者不在電腦前，中樞已**自動拒絕**（只拒絕、不代按允許），"
                       f"並請它改做別的或找 Claude／Codex 代辦。使用者回來若需要，再授權一次。")
        except Exception as e:
            first["err"] = f"{type(e).__name__} {e}"[:200]
    st = {k: v for k, v in st.items() if k in ids or now - v.get("first", now) < 86400}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for msg in out:
        d = r"H:\Agent共用\inbox"
        fn = time.strftime("%m%d-%H%M%S-權限逾時.txt")
        open(os.path.join(d, fn + ".tmp"), "w", encoding="utf-8").write(f"from: 系統權限\nto: none\n---\n{msg}\n")
        os.replace(os.path.join(d, fn + ".tmp"), os.path.join(d, fn))
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("使用者不在：", user_away())
    print(tick())
