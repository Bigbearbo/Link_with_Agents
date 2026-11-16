"""三方討論中樞（多房間版）：電腦網頁 + Telegram。

- 每個 Telegram 群組是一個獨立「房間」：各自的討論紀錄、各自的 Claude / Codex 對話。
  房間之間訊息不互通，回覆只送回該房間綁定的群組。主人私訊 bot 歸到「主討論」房。
- 網頁上方用分頁切換房間；網頁打的字只進目前選的房間，並轉貼到該房間的群組。
- Telegram 只聽配對過的主人；網頁只綁 127.0.0.1，且需金鑰（secrets/web-key.txt）。
- 寫檔權限是全域設定；Claude 在可寫模式下用 auto 權限，安全相關動作要主人在該房間同意。
"""
import json
import os
import random
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agents as rt  # noqa: E402
from secret_store import load_token  # noqa: E402

STATE = os.path.join(HERE, "tg_state.json")
LOG_DIR = os.path.join(HERE, "logs")
UI_FILE = os.path.join(HERE, "ui.html")
PORT = 8765
MAIN = "main"
ICON = {"使用者": "🧑", "Claude": "🟠", "Codex": "🟢", "系統": "⚙️"}
TG_HELP = ("直接說話：Claude 和 Codex 都會發表意見（每個群組是獨立的討論房）\n"
           "/claude 問題、/codex 問題：只問一位\n/more 2：讓他們再討論 2 輪\n"
           "/order 換誰先講　/stop 中斷　/new 這個房間開新討論\n/write on 或 off：開關寫檔權限\n"
           "/allow、/deny：同意或拒絕安全相關請求\n/unbind 這個群組靜音　/bind 重新啟用")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Bot:
    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, _http_timeout=40, **params):
        req = urllib.request.Request(self._base + method, data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_http_timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:  # 不印含 token 的 URL
            try:
                data = json.loads(e.read() or b"{}")
            except Exception:
                data = {}
            raise RuntimeError(f"Telegram {method} 失敗：{data.get('description', e.code)}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise RuntimeError(f"Telegram {method} 連線失敗：{getattr(e, 'reason', e)}") from None
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} 失敗：{data.get('description')}")
        return data["result"]

    def send(self, chat_id, text):
        for i in range(0, max(len(text), 1), 4000):
            self.call("sendMessage", chat_id=chat_id, text=text[i:i + 4000] or "（空白）")


class Room:
    """一個獨立的討論房。"""

    def __init__(self, hub, rid, d):
        self.hub, self.id = hub, rid
        self.name = d.get("name") or "討論房"
        self.chats = list(d.get("chats") or [])
        self.transcript = list(d.get("transcript") or [])
        self.epoch = int(d.get("epoch") or 1)
        self.order = d.get("order") or "claude"
        self.approval = d.get("approval")
        self.log_name = d.get("log_name")
        self.claude, self.codex = rt.ClaudeAgent("Claude"), rt.CodexAgent("Codex")
        self.claude.session, self.claude.seen = d.get("claude_session"), int(d.get("claude_seen") or 0)
        self.codex.session, self.codex.seen = d.get("codex_session"), int(d.get("codex_seen") or 0)
        self.stop_flag = False

    def dump(self):
        return {"name": self.name, "chats": self.chats, "transcript": self.transcript, "epoch": self.epoch,
                "order": self.order, "approval": self.approval, "log_name": self.log_name,
                "claude_session": self.claude.session, "claude_seen": self.claude.seen,
                "codex_session": self.codex.session, "codex_seen": self.codex.seen}

    def write_log(self):
        if not self.transcript:
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        if not self.log_name:
            safe = re.sub(r'[\\/:*?"<>|\s]+', "_", self.name)[:30]
            self.log_name = f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.md"
        with open(os.path.join(LOG_DIR, self.log_name), "w", encoding="utf-8") as f:
            f.write(f"# 三方討論：{self.name}\n\n")
            for m in self.transcript:
                f.write(f"## {m['who']}（{m.get('src', '')}）{m['time']}\n\n{m['text']}\n\n")

    # ---------- Telegram 目標 ----------
    def targets(self):
        if self.chats:
            return list(self.chats)
        owner = self.hub.state.get("owner")
        return [owner] if (self.id == MAIN and owner) else []

    def tg_broadcast(self, text):
        for cid in self.targets():
            try:
                self.hub.bot.send(cid, text)
            except Exception as e:
                log(f"[{self.name}] 轉發到 Telegram 失敗：{e}")

    def typing(self):
        for cid in self.targets():
            try:
                self.hub.bot.call("sendChatAction", chat_id=cid, action="typing")
            except Exception:
                pass

    # ---------- 紀錄 ----------
    def add(self, who, text, src):
        """src: 電腦 / Telegram / 系統 / 回覆。非 Telegram 來源會轉貼到本房間的群組。"""
        with self.hub.lock:
            self.transcript.append({"who": who, "text": text, "src": src, "time": time.strftime("%m-%d %H:%M")})
            self.hub.save()
        if src != "Telegram":
            label = "主人（電腦）" if who == "使用者" else who
            threading.Thread(target=self.tg_broadcast, args=(f"{ICON.get(who, '')} {label}\n\n{text}",),
                             daemon=True).start()

    def notice(self, text):
        self.add("系統", text, "系統")

    # ---------- 狀態 ----------
    def agents(self):
        return (self.claude, self.codex)

    def order_both(self):
        return [self.claude, self.codex] if self.order == "claude" else [self.codex, self.claude]

    @property
    def busy(self):
        return self.claude.busy or self.codex.busy

    @property
    def status(self):
        active = [a.name for a in self.agents() if a.busy]
        return "、".join(active) + " 工作中…" if active else "閒置"

    # ---------- 操作 ----------
    def user_says(self, text, src, target="both"):
        if target in ("claude", "codex"):  # 標記只問誰，另一位之後讀到記錄才不會搞錯
            text = f"（只問 {'Claude' if target == 'claude' else 'Codex'}）{text}"
        self.add("使用者", text, src)
        self.stop_flag = False
        self.chain({"both": self.order_both(), "claude": [self.claude], "codex": [self.codex]}[target])

    def more(self, n):
        if self.busy or not self.transcript:
            return
        self.stop_flag = False
        self.chain(self.order_both() * max(1, min(5, n)))

    def toggle_order(self):
        with self.hub.lock:
            self.order = "codex" if self.order == "claude" else "claude"
            self.hub.save()
        return self.order

    def new_chat(self):
        if self.busy:
            return False
        with self.hub.lock:
            self.claude, self.codex = rt.ClaudeAgent("Claude"), rt.CodexAgent("Codex")
            self.transcript, self.approval, self.log_name = [], None, None
            self.epoch += 1
            self.hub.save()
        threading.Thread(target=self.tg_broadcast, args=("⚙️ 這個房間已開新討論。",), daemon=True).start()
        return True

    def stop(self):
        self.stop_flag = True
        for a in self.agents():
            a.pending = False
            if a.proc:
                try:
                    a.proc.kill()
                except Exception:
                    pass

    # ---------- 回答：兩位各自獨立，不互相排隊 ----------
    def chain(self, seq):
        """依序請 seq 裡的 agent 發言。空著的馬上開始，前一位答完才換下一位；
        忙碌中的那位不擋後面的人：記一筆「待回應」，手上的事做完再回一輪，看到中間所有新訊息。"""
        while seq and not self.stop_flag:
            agent, rest = seq[0], seq[1:]
            with self.hub.lock:
                if agent.busy:
                    agent.pending = True
                    seq = rest
                    continue
                agent.busy = True
            threading.Thread(target=self.agent_loop, args=(agent, lambda r=rest: self.chain(r)),
                             daemon=True).start()
            return

    def agent_loop(self, agent, then):
        try:
            while not self.stop_flag:
                self.run_turn(agent)
                with self.hub.lock:
                    if agent.pending and not self.stop_flag:
                        agent.pending = False
                        continue
                    break
        finally:
            with self.hub.lock:
                agent.busy = False
            if self.stop_flag:
                if not self.busy:
                    self.notice("已停止。")
            elif then:
                then()

    def run_turn(self, agent):
        threading.Thread(target=self.typing, daemon=True).start()
        with self.hub.lock:
            snapshot = list(self.transcript)
        agent.write = bool(self.hub.state.get("write_mode"))
        prompt = agent.build_prompt(snapshot)
        if agent.session is None and self.id != MAIN:
            prompt = f"（這是「{self.name}」討論房，和其他房間的討論互相獨立。）\n" + prompt
        log(f"[{self.name}] {agent.name} 開始工作…")
        t0 = time.time()
        try:
            reply = agent.run(prompt)
        except Exception as e:
            if not self.stop_flag:
                log(f"[{self.name}] {agent.name} 失敗：{e}")
                self.notice(f"{agent.name} 失敗：{str(e)[:500]}")
            return
        finally:
            agent.started = None
        agent.seen = len(snapshot)  # 自己的發言會被過濾；途中新增的訊息下次看得到
        agent.extra_allow = []      # 允許一次：用完即清
        self.add(agent.name, reply, "回覆")
        log(f"[{self.name}] {agent.name} 完成（{time.time() - t0:.0f} 秒）")
        if getattr(agent, "denials", None):
            self.request_approval(agent)

    # ---------- 安全相關動作：請主人同意 ----------
    def request_approval(self, agent):
        items, rules = [], []
        for d in agent.denials[:5]:
            text, rule = rt.describe_denial(d)
            if rule not in rules:
                items.append(text)
                rules.append(rule)
        agent.denials = []
        with self.hub.lock:
            self.approval = {"agent": agent.name, "items": items, "rules": rules, "id": secrets.token_hex(4)}
            self.hub.save()
        body = "\n".join(f"• {t}" for t in items)
        self.notice(f"🔐 {agent.name} 請求同意：\n{body}\n\n允許一次請按「允許」或傳 /allow，拒絕請按「拒絕」或傳 /deny。")

    def answer_approval(self, allow, src):
        with self.hub.lock:
            ap, self.approval = self.approval, None
            self.hub.save()
        if not ap:
            return False
        agent = self.claude if ap["agent"] == "Claude" else self.codex
        items = "、".join(ap["items"])
        if allow:
            agent.extra_allow = list(ap["rules"])
            self.add("使用者", f"（只問 {agent.name}）我允許你這一次執行：{items}。請繼續剛才的工作。", src)
        else:
            self.add("使用者", f"（只問 {agent.name}）我不允許：{items}。請改用別的做法，或說明還需要什麼。", src)
        self.stop_flag = False
        self.chain([agent])
        return True

    # ---------- 網頁 ----------
    def snapshot(self, since, epoch):
        t = self.transcript
        reset = epoch != self.epoch
        start = 0 if reset else max(0, min(since, len(t)))
        return {"room": self.id, "epoch": self.epoch, "reset": reset, "start": start, "messages": t[start:],
                "total": len(t), "busy": self.busy, "status": self.status, "order": self.order,
                "approval": self.approval,
                "progress": [{"who": a.name, "steps": list(a.steps), "pending": a.pending,
                              "elapsed": int(time.time() - (a.started or time.time()))}
                             for a in self.agents() if a.busy]}


class Hub:
    def __init__(self):
        self.bot = Bot(load_token())
        self.state = {"offset": 0, "owner": None, "muted_groups": [], "write_mode": False}
        if os.path.exists(STATE):
            with open(STATE, encoding="utf-8") as f:
                self.state.update(json.load(f))
        self.lock = threading.RLock()
        self.pair_code = None
        self.key = web_key()
        self.tg_ok = False
        self.health = {}
        self.rooms = {}
        self._load_rooms()

    def _load_rooms(self):
        s = self.state
        if "rooms" not in s:  # 從單一討論的舊格式升級：舊紀錄成為主討論，第一個群組歸主討論，其餘群組各自一房
            groups = list(s.get("groups") or [])
            s["rooms"] = {MAIN: {"name": "主討論", "chats": groups[:1], "transcript": s.get("transcript", []),
                                 "epoch": s.get("epoch", 1), "order": s.get("order", "claude"),
                                 "approval": s.get("approval"), "log_name": s.get("log_name"),
                                 "claude_session": s.get("claude_session"), "claude_seen": s.get("claude_seen", 0),
                                 "codex_session": s.get("codex_session"), "codex_seen": s.get("codex_seen", 0)}}
            for i, g in enumerate(groups[1:], start=2):
                s["rooms"][f"g{abs(g)}"] = {"name": f"群組 {i}", "chats": [g]}
            for k in ("groups", "transcript", "epoch", "order", "approval", "log_name", "claude_session",
                      "claude_seen", "codex_session", "codex_seen"):
                s.pop(k, None)
            log("已升級成多房間格式。")
        s["rooms"].setdefault(MAIN, {"name": "主討論", "chats": []})
        self.rooms = {rid: Room(self, rid, d) for rid, d in s["rooms"].items()}
        self.save()

    def save(self):
        with self.lock:
            self.state["rooms"] = {rid: r.dump() for rid, r in self.rooms.items()}
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATE)
            for r in self.rooms.values():
                r.write_log()

    def room(self, rid):
        return self.rooms.get(rid) or self.rooms[MAIN]

    def room_for_chat(self, cid, title=None, create=True):
        with self.lock:
            for r in self.rooms.values():
                if cid in r.chats:
                    if title and r.name.startswith("群組 ") and r.name != title:
                        r.name = title
                        self.save()
                    return r
            if not create:
                return None
            rid = f"g{abs(cid)}"
            self.rooms[rid] = Room(self, rid, {"name": title or "新群組", "chats": [cid]})
            self.save()
            log(f"新增討論房：{title or rid}")
            return self.rooms[rid]

    def set_write(self, on):
        on = bool(on)
        if on == bool(self.state.get("write_mode")):
            return on
        with self.lock:
            self.state["write_mode"] = on
            self.save()
        log("權限：" + ("可寫" if on else "唯讀"))
        for r in list(self.rooms.values()):
            if r.transcript:
                r.notice(rt.PERM_NOTE[on].strip())
        return on

    # ---------- Telegram ----------
    def tg_loop(self):
        while True:
            try:
                me = self.bot.call("getMe")
                break
            except Exception as e:
                log(f"連不上 Telegram，5 秒後重試：{e}")
                time.sleep(5)
        self.username, self.bot_id = me["username"], me["id"]
        self.tg_ok = True
        log(f"已連上 Telegram bot @{self.username}")
        if not self.state["owner"]:
            self.pair_code = f"{random.SystemRandom().randint(0, 999999):06d}"
            log(f"尚未配對。請在 Telegram 私訊 @{self.username} 傳送：  /pair {self.pair_code}")
        while True:
            try:
                updates = self.bot.call("getUpdates", _http_timeout=60, **{
                    "offset": self.state["offset"], "timeout": 50, "allowed_updates": ["message"]})
            except RuntimeError as e:
                log(str(e))
                time.sleep(5)
                continue
            for u in updates:
                with self.lock:
                    self.state["offset"] = u["update_id"] + 1
                    self.save()
                try:
                    self.tg_handle(u.get("message") or {})
                except Exception as e:
                    log(f"處理 Telegram 訊息失敗：{e}")

    def tg_handle(self, msg):
        chat, sender, text = msg.get("chat", {}), msg.get("from", {}), (msg.get("text") or "").strip()
        if sender.get("is_bot") or not text:
            return
        cid, ctype = chat["id"], chat.get("type")
        owner = self.state["owner"]
        if not owner:
            if ctype == "private" and self.pair_code and text == f"/pair {self.pair_code}" and sender.get("id") == cid:
                with self.lock:
                    self.state["owner"] = cid
                    self.save()
                self.pair_code = None
                log("配對成功。")
                self.bot.send(cid, "配對成功！\n\n" + TG_HELP)
            return
        if sender.get("id") != owner:
            return  # 只聽主人
        mention = f"@{self.username}"
        first = text.split(" ", 1)[0]
        if first.startswith("/") and "@" in first and not first.lower().endswith(mention.lower()):
            return  # 給別的 bot 的指令
        text = re.sub(re.escape(mention), "", text, flags=re.I).strip()
        word = text.split(" ", 1)[0].split("@")[0].lower() if text.startswith("/") else ""
        arg = text.split(" ", 1)[1].strip() if " " in text else ""

        if ctype in ("group", "supergroup"):
            muted = self.state["muted_groups"]
            if word == "/unbind":
                with self.lock:
                    r = self.room_for_chat(cid, create=False)
                    if r:
                        r.chats.remove(cid)
                    if cid not in muted:
                        muted.append(cid)
                    self.save()
                self.bot.send(cid, "⚙️ 已靜音，這個群組不再同步。傳 /bind 重新啟用（討論紀錄會保留）。")
                return
            if cid in muted:
                if word != "/bind":
                    return
                with self.lock:
                    muted.remove(cid)
                    self.save()
            room = self.room_for_chat(cid, chat.get("title"))
            if word in ("/bind", "/start"):
                self.bot.send(cid, f"⚙️ 這個群組是獨立的討論房「{room.name}」，訊息不會和其他群組混在一起。\n\n" + TG_HELP)
                return
        elif ctype == "private" and cid == owner:
            room = self.rooms[MAIN]
        else:
            return

        if not text:
            return
        if word == "/stop":
            room.stop()
        elif word == "/status":
            self.bot.send(cid, f"⚙️ 「{room.name}」{room.status}｜{'Claude' if room.order == 'claude' else 'Codex'} 先講"
                               f"｜本房 {len(room.transcript)} 則")
        elif word == "/new":
            if not room.new_chat():
                self.bot.send(cid, "⚙️ 還在回答中，先 /stop 再開新討論。")
        elif word in ("/allow", "/deny"):
            if not room.answer_approval(word == "/allow", "Telegram"):
                self.bot.send(cid, "⚙️ 這個房間目前沒有待同意的請求。")
        elif word == "/write":
            if arg.lower() in ("on", "off", "開", "關"):
                self.set_write(arg.lower() in ("on", "開"))
            else:
                self.bot.send(cid, f"⚙️ 目前{'可寫' if self.state.get('write_mode') else '唯讀'}。用 /write on 或 /write off 切換。")
        elif word == "/order":
            o = room.toggle_order()
            self.bot.send(cid, f"⚙️ 改成 {'Claude' if o == 'claude' else 'Codex'} 先講。")
        elif word == "/more":
            room.more(int(arg) if arg.isdigit() else 1)
        elif word in ("/claude", "/codex"):
            if arg:
                room.user_says(arg, "Telegram", word[1:])
        elif word in ("/help", "/start"):
            self.bot.send(cid, TG_HELP)
        elif word:
            self.bot.send(cid, "⚙️ 不認得的指令。\n\n" + TG_HELP)
        else:
            room.user_says(text, "Telegram")

    # ---------- 網頁 ----------
    def snapshot(self, rid, since, epoch):
        with self.lock:
            r = self.room(rid)
            d = r.snapshot(since, epoch)
            d["rooms"] = [{"id": x.id, "name": x.name, "busy": x.busy, "total": len(x.transcript),
                           "approval": bool(x.approval), "linked": bool(x.targets())}
                          for x in self.rooms.values()]
            d["write"] = bool(self.state.get("write_mode"))
            d["health"] = {k: {"ok": v[0], "msg": v[1]} for k, v in self.health.items()}
            d["telegram"] = {"ok": self.tg_ok, "paired": bool(self.state["owner"]), "pair_code": self.pair_code,
                             "bot": getattr(self, "username", None), "linked": bool(r.targets())}
            return d


def make_handler(hub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self, q=None):
            k = self.headers.get("X-Key") or (q or {}).get("k", [""])[0]
            return secrets.compare_digest(k, hub.key)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                if not self._authed(q):
                    return self._json({"error": "請用啟動程式開啟的網址"}, 403)
                with open(UI_FILE, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/poll":
                if not self._authed():
                    return self._json({"error": "forbidden"}, 403)
                self._json(hub.snapshot(q.get("room", [MAIN])[0], int(q.get("since", ["0"])[0]),
                                        int(q.get("epoch", ["0"])[0])))
            elif u.path == "/ping":
                self._json({"app": "roundtable-hub"})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._authed():
                return self._json({"error": "forbidden"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            p = urlparse(self.path).path
            room = hub.room(data.get("room") or MAIN)
            if p == "/api/send":
                text = (data.get("text") or "").strip()
                if text:
                    room.user_says(text, "電腦", data.get("target") if data.get("target") in ("claude", "codex") else "both")
                return self._json({"ok": True})
            if p == "/api/more":
                room.more(int(data.get("n") or 1))
                return self._json({"ok": True})
            if p == "/api/stop":
                room.stop()
                return self._json({"ok": True})
            if p == "/api/new":
                return self._json({"ok": room.new_chat()})
            if p == "/api/approve":
                return self._json({"ok": room.answer_approval(bool(data.get("allow")), "電腦")})
            if p == "/api/write":
                return self._json({"write": hub.set_write(data.get("on"))})
            if p == "/api/order":
                return self._json({"order": room.toggle_order()})
            self._json({"error": "not found"}, 404)

    return H


def web_key():
    """網頁金鑰固定存在 secrets/web-key.txt，重啟後原本的分頁可以繼續用。"""
    path = os.path.join(HERE, "secrets", "web-key.txt")
    try:
        with open(path, encoding="utf-8") as f:
            k = f.read().strip()
        if len(k) >= 16:
            return k
    except FileNotFoundError:
        pass
    k = secrets.token_urlsafe(16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(k)
    return k


def already_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == "roundtable-hub"
    except Exception:
        return False


if __name__ == "__main__":
    os.system("title 三方討論中樞（關閉此視窗即停止）")
    if already_running():
        print("三方討論中樞已經在執行了，幫你打開網頁。")
        webbrowser.open(f"http://127.0.0.1:{PORT}/?k={web_key()}")
        sys.exit(0)
    hub = Hub()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(hub))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/?k={hub.key}"
    log(f"網頁：{url}")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    if hub.state.get("workdir") != rt.WORKDIR:
        for r in hub.rooms.values():
            if r.transcript:
                r.notice(rt.WORKDIR_NOTE)
        hub.state["workdir"] = rt.WORKDIR
        hub.save()
    if "--write" in sys.argv:
        hub.set_write(True)

    def run_check():
        log("自我檢查中（Claude 回話、Codex 執行指令）…")
        hub.health = rt.self_check()
        for k, (ok, msg) in hub.health.items():
            log(f"自我檢查 {k}：{'正常' if ok else '有問題 → ' + msg}")
    threading.Thread(target=run_check, daemon=True).start()
    for r in hub.rooms.values():  # 上次中斷或失敗、還沒回答的訊息：請沒回到的那位補答
        t = r.transcript
        last_user = max((i for i, m in enumerate(t) if m["who"] == "使用者"), default=None)
        if last_user is None:
            continue
        text = t[last_user]["text"]
        wanted = ([r.claude] if text.startswith("（只問 Claude）") else
                  [r.codex] if text.startswith("（只問 Codex）") else r.order_both())
        answered = {m["who"] for m in t[last_user + 1:] if m["who"] in ("Claude", "Codex")}
        todo = [a for a in wanted if a.name not in answered]
        if todo:
            log(f"[{r.name}] 有訊息還沒回答到，請 {'、'.join(a.name for a in todo)} 補答。")
            r.chain(todo)
    hub.tg_loop()
