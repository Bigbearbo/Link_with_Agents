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
                r = "它正在忙，2 分鐘後單還在就再叫一次"
                # Gemini 11-20 00:20：21:41 被跳過後沒人再點名，單變成孤兒掛到半夜 → 2 分鐘後還在、它也不忙了就補叫
                def retry(name=self.name, rid=self.room_id or "main", path=p, k=key):
                    if os.path.exists(path) and not opencode_busy(name):
                        LAST_NUDGE[k] = time.time()
                        rr = nudge(name, rid, again=True)
                        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "叫醒.log"), "a", encoding="utf-8") as f:
                            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {name} {rid} 忙完補叫 {rr}\n")
                import threading as _th
                _th.Timer(120, retry).start()
            else:
                LAST_NUDGE[key] = time.time()
                r = nudge(self.name, self.room_id or "main", again=not new)
                if r == "已送進視窗":  # 11-19 04:17：Gemini 本來就叫不醒，不算次數，免得誤報「叫了 3 次沒回」
                    NUDGE_COUNT[key] = n + 1
                    import threading as _th
                    _th.Thread(target=forward_unsent, args=(self.name, self.room_id or "main", p), daemon=True).start()
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


USER_RESERVE = 100   # 每天保留給使用者的 100 次（OpenRouter 免費 1000 次＝輝達 900＋使用者 100）


def nvidia_dispatch_limit():
    """派工員今天最多用到幾次（使用者 11-19 07:15：保留給使用者的 100 次，最後 4 小時用公式還給輝達）。
    換日在台北 08:00。離換日 >4 小時：只派到 900 的 70%（留給人找它）。
    最後 4 小時：900 全開，再加上動態釋放的保留額（見下方公式）；
    回傳 (上限, 已釋放保留次數, 距換日小時)。"""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    left = (nxt - now).total_seconds() / 3600
    if left > 4:
        return int(NVIDIA_DAILY_BUDGET * 0.7), 0, left
    # 使用者 07:17 改成動態：四段各還 10%、20%、30%、40%，每段在那一小時內線性還完；
    # 例：剩 20 分鐘時，最後一段 40% 還剩 1/3 沒還＝留 40%×0.33 給使用者。到換日全部還完。
    k = 4 - left                          # 已進入最後 4 小時多久（0～4）
    done_chunks, part = int(k), k - int(k)
    chunks = [0.1, 0.2, 0.3, 0.4]
    frac = sum(chunks[:done_chunks]) + (chunks[done_chunks] * part if done_chunks < 4 else 0)
    rel = int(USER_RESERVE * frac)
    return NVIDIA_DAILY_BUDGET + rel, rel, left


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


SKIP_FWD = ("沒有新訊息", "已讀到最新", "不需要回", "無需回覆", "不用回")


def forward_unsent(name, room_id, ticket, wait=600):
    """使用者 11-20 06:19「輝達今天的進度出錯了」——查 OpenCode 紀錄：06:08 起它有讀新訊息、也在視窗裡寫了回答，
    但沒寫發送箱，所以群裡看不到。叫醒後等它跑完（最多 10 分鐘）；單還在（＝它沒在房間發言）就把它這次在視窗
    寫的最後一段回答代轉進發送箱，中樞照常貼出、銷單；「沒有新訊息」之類的不轉。"""
    import base64, json, urllib.request
    t0 = time.time()
    time.sleep(20)
    while time.time() - t0 < wait and opencode_busy(name):
        time.sleep(10)
    time.sleep(15)   # 它自己寫發送箱、中樞貼出要一點時間
    if not os.path.exists(ticket):
        return
    try:
        c = json.load(open(NOTIFY[name], encoding="utf-8"))
        auth = "Basic " + base64.b64encode(f"opencode:{c['password']}".encode()).decode()
        url = f"http://127.0.0.1:{c['port']}"
        get = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(url + p, headers={"Authorization": auth}), timeout=10).read())
        ss = sorted(get("/session"), key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
        msgs = get(f"/session/{ss[0]['id']}/message") if ss else []
        texts = [p.get("text", "") for m in msgs if m.get("info", {}).get("role") == "assistant"
                 and m.get("info", {}).get("time", {}).get("created", 0) / 1000 >= t0
                 for p in m.get("parts", []) if p.get("type") == "text" and p.get("text", "").strip()]
        text = texts[-1].strip() if texts else ""
        if len(text) < 40 or any(s in text for s in SKIP_FWD):
            return
        out = os.path.join(HOMES[name], "群組", "發送", f"回覆_代轉{time.strftime('%H%M%S')}.json")
        with open(out + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"room": room_id, "text": text + "\n\n（中樞代轉：寫在視窗裡、沒送出的回答）"}, f, ensure_ascii=False)
        os.replace(out + ".tmp", out)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "叫醒.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {name} {room_id} 沒送出，代轉 {len(text)} 字\n")
    except Exception:
        pass


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
