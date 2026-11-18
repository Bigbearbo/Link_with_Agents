"""住在自己視窗裡的 agent（目前是 Gemini，使用者 11-18 20:11 移交到 agy 視窗）。
中樞不再用 agy -p 在背景叫它（官方 FAQ：消費方案不支援把 agy 包進應用程式），改成留單：
每個房間一個檔 H:\\Gemini用\\群組\\待回\\<房間id>.md，有人叫它就把最近的訊息附上去；
視窗裡的 Gemini 用 群組.py 看群組、回完把單移到 待回\\已處理\\。
"""
import os, time

import agents as rt

TICKETS = r"H:\Gemini用\群組\待回"
HOMES = {"Gemini": r"H:\Gemini用", "輝達": r"H:\免費模型用"}  # 輝達 11-18 21:2x 也搬進自己的視窗（OpenCode）


class WindowAgent(rt.Agent):
    """run() 不呼叫模型，只留單；回傳空字串，中樞就不會替它發言。"""

    def __init__(self, name, room_id="", room_name=""):
        super().__init__(name)
        self.room_id, self.room_name = room_id, room_name

    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass

    def build_prompt(self, transcript):
        new = [m for m in transcript[self.seen:] if m["who"] != self.name][-8:]
        return "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text'][:1500]}" for m in new)

    def run(self, prompt):
        self.steps, self.started, self.said = [], time.time(), []
        self.step("留單給視窗")
        home = HOMES.get(self.name)
        tickets = os.path.join(home, "群組", "待回") if home else TICKETS
        os.makedirs(tickets, exist_ok=True)
        p = os.path.join(tickets, f"{self.room_id or 'main'}.md")
        head = "" if os.path.exists(p) else (f"# 待回：{self.room_name or self.room_id}（房間 id：{self.room_id}）\n"
                                             f"看完整對話：PY {home or 'H:'}\\群組\\群組.py 看 {self.room_id}；回完把這個檔移到 已處理\\\n")
        new = not os.path.exists(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(head + f"\n## {time.strftime('%m-%d %H:%M')} 有人找你\n{prompt}\n")
        if new:   # 同一張單還沒處理就不重複叫，免得一直插話
            self.step("叫醒視窗：" + nudge(self.name, self.room_id or "main"))
        return ""


NOTIFY = {"輝達": r"H:\Claude用\三方討論\secrets\opencode-輝達.json"}  # 開輝達視窗.py 開窗時寫、關窗時刪


LAST_ROOM = {}  # 最後一次叫醒是為了哪個房間；OpenCode 忙碌時「工作中」就顯示在那個房間


def opencode_busy(name):
    """OpenCode 視窗現在有沒有在跑（/session/status 有非 idle 的 session）。沒開視窗回 None。"""
    import base64, json, urllib.request
    f = NOTIFY.get(name)
    if not f or not os.path.exists(f):
        return None
    try:
        c = json.load(open(f, encoding="utf-8"))
        auth = "Basic " + base64.b64encode(f"opencode:{c['password']}".encode()).decode()
        r = urllib.request.Request(f"http://127.0.0.1:{c['port']}/session/status", headers={"Authorization": auth})
        st = json.loads(urllib.request.urlopen(r, timeout=3).read())
        vals = st.values() if isinstance(st, dict) else st
        return any(isinstance(v, dict) and v.get("type") not in (None, "idle") for v in vals)
    except Exception:
        return None


def nudge(name, room_id):
    """叫醒住在 OpenCode 視窗的 agent：往它的輸入框塞一句話並送出（使用者 11-19 要的通知功能）。
    agy（Gemini）沒有這種控制介面，只留單。回傳結果文字，不丟例外。"""
    import base64, json, urllib.request
    f = NOTIFY.get(name)
    if not f or not os.path.exists(f):
        return "沒有開視窗或不支援，只留單"
    LAST_ROOM[name] = room_id
    try:
        c = json.load(open(f, encoding="utf-8"))
        auth = "Basic " + base64.b64encode(f"opencode:{c['password']}".encode()).decode()
        url = f"http://127.0.0.1:{c['port']}"
        text = (f"【中樞通知】群組有人找你：讀 群組\\待回\\{room_id}.md，"
                f"用 群組.py 看 {room_id} 和回覆，回完把那張單移到 群組\\待回\\已處理\\。")
        for path, body in (("/tui/append-prompt", {"text": text}), ("/tui/submit-prompt", {})):
            r = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                       headers={"Content-Type": "application/json", "Authorization": auth})
            urllib.request.urlopen(r, timeout=5).read()
        return "已送進視窗"
    except Exception as e:
        return f"叫不醒（{type(e).__name__}），只留單"
