"""住在自己視窗裡的 agent（目前是 Gemini，使用者 11-18 20:11 移交到 agy 視窗）。
中樞不再用 agy -p 在背景叫它（官方 FAQ：消費方案不支援把 agy 包進應用程式），改成留單：
每個房間一個檔 H:\\Gemini用\\群組\\待回\\<房間id>.md，有人叫它就把最近的訊息附上去；
視窗裡的 Gemini 用 群組.py 看群組、回完把單移到 待回\\已處理\\。
"""
import json, os, time

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
                                             f"看完整對話：PY {home or 'H:'}\\群組\\群組.py 看 {self.room_id}\n"
                                             f"回覆（照抄、只改 text）：寫檔 {home or 'H:'}\\群組\\發送\\回覆.json ＝ "
                                             + json.dumps({"room": self.room_id or "main", "text": "你的回覆"}, ensure_ascii=False)
                                             + "；中樞貼出後自動銷單\n")
        new = not os.path.exists(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(head + f"\n## {time.strftime('%m-%d %H:%M')} 有人找你\n{prompt}\n")
        # 11-19 03:28 使用者：「輝達好像沒收到消息」——原本只有「新單」才叫，輝達回完沒把單移走，
        # 之後同一張單一直追加卻不再叫。改成：新單、或距離上次叫醒超過 NUDGE_GAP 秒就再叫一次（避免一直插話）。
        # DeepSeek 03:29：同一張單最多叫 NUDGE_MAX 次（避免它慢時疊出重複處理）；它在忙就先不叫
        key = (self.name, self.room_id or "main")
        if new:
            NUDGE_COUNT[key] = 0
        n = NUDGE_COUNT.get(key, 0)
        if (new or time.time() - LAST_NUDGE.get(key, 0) > NUDGE_GAP) and n < NUDGE_MAX:
            if opencode_busy(self.name):
                r = "它正在忙，這次先不叫（下次有人找它時再試）"
            else:
                LAST_NUDGE[key] = time.time()
                r = nudge(self.name, self.room_id or "main", again=not new)
                if r == "已送進視窗":  # 11-19 04:17：Gemini 本來就叫不醒，不算次數，免得誤報「叫了 3 次沒回」
                    NUDGE_COUNT[key] = n + 1
            self.step("叫醒視窗：" + r)
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "叫醒.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%m-%d %H:%M:%S')} {self.name} {self.room_id or 'main'} 第{NUDGE_COUNT.get(key, 0)}次 {r}\n")
        return ""


LAST_NUDGE = {}
NUDGE_COUNT = {}
NUDGE_GAP = 120
NUDGE_MAX = 3


NOTIFY = {"輝達": r"H:\Claude用\三方討論\secrets\opencode-輝達.json"}  # 開輝達視窗.py 開窗時寫、關窗時刪


NVIDIA_DAILY_BUDGET = 900   # 輝達每天呼叫次數預算＝跟 worker 同一個 900（OpenRouter 免費模型按次數限；使用者 11-19 06:50）
OPENCODE_LOG = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode", "log", "opencode.log")


def nvidia_calls_today():
    """數 OpenCode 紀錄裡「OpenRouter 的今天」（UTC 日＝台北 08:00 換日，跟 worker 和用量條一致；使用者 06:56）的模型呼叫次數（每行 message=stream provider＝一次呼叫，含子代理和標題小模型）。
    同時寫 H:\\Agent共用\\輝達-用量.json 給大家看。"""
    import datetime, json
    tz = datetime.timezone(datetime.timedelta(hours=8))
    today = datetime.datetime.now(datetime.timezone.utc).date()
    n = small = 0
    try:
        with open(OPENCODE_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "message=stream provider" not in line or not line.startswith("timestamp="):
                    continue
                ts = datetime.datetime.fromisoformat(line[10:34].replace("Z", "+00:00"))
                if ts.date() == today:
                    n += 1
                    small += "small=true" in line
    except OSError:
        return None
    rec = {"更新": datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M"), "今天呼叫": n, "其中小模型": small,
           "每日預算": NVIDIA_DAILY_BUDGET, "比例": round(n / NVIDIA_DAILY_BUDGET, 3),
           "說明": "OpenCode 這邊的次數（UTC 日，台北 08:00 換日）；聊天室用量條＝這個＋worker 的次數，共用 900。派工員超過 70% 就不再派，90% 群組提醒。預算改 window_agent.NVIDIA_DAILY_BUDGET。"}
    try:
        with open(r"H:\Agent共用\輝達-用量.json", "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return n


def nvidia_total_today():
    """輝達今天（UTC 日，台北 08:00 換日）的免費模型呼叫總數＝worker（ask.py 等）＋OpenCode。
    用量條和派工員都只呼叫這一個（使用者 06:58：一份來源，不要兩個地方各算）。回傳 (合計, worker, OpenCode)；讀不到回 None。"""
    import datetime, json
    oc = nvidia_calls_today()
    if oc is None:
        return None
    worker = 0
    try:
        paths = json.load(open(r"H:\Agent共用\ai-call\paths.json", encoding="utf-8"))
        b = json.load(open(paths.get("budget", r"D:\Codex用\work\openrouter-worker\budget.json"), encoding="utf-8"))
        if b.get("day") == datetime.datetime.now(datetime.timezone.utc).date().isoformat():
            worker = int(b.get("attempts", 0))
    except (OSError, ValueError):
        pass
    # 11-19 07:05 使用者看 OpenRouter 網站：今天 407＋3＝410，跟 OpenCode 自己數的 408 對得上；
    # worker 那 97 次幾乎都在 11-18 21:05 換鑰匙「之前」，算在舊鑰匙（現在給 DeepSeek 的那把）上，不該加。
    # 所以合計只算 OpenCode；worker 次數另外回傳，給人看，不進預算。
    return oc, worker, oc


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


def nudge(name, room_id, again=False, custom=None):
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
        text = custom or (f"【中樞通知】房間 {room_id}{'（主討論）' if room_id == 'main' else ''} 有人找你：用 群組.py 看 {room_id}（只給你沒看過的新訊息），"
                f"需要你回的才回，**回覆要發在同一個房間 {room_id}**（發送箱 room 寫 {room_id}）；中樞會自動銷單。"
                + ("這張單之前就在了、可能很長，不用整張重讀。" if again else ""))
        for path, body in (("/tui/append-prompt", {"text": text}), ("/tui/submit-prompt", {})):
            r = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                       headers={"Content-Type": "application/json", "Authorization": auth})
            urllib.request.urlopen(r, timeout=5).read()
        return "已送進視窗"
    except Exception as e:
        return f"叫不醒（{type(e).__name__}），只留單"
