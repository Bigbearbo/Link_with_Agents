"""三方討論中樞（多房間版）：電腦網頁 + Telegram。

- 每個 Telegram 群組是一個獨立「房間」：各自的討論紀錄、各自的 Claude / Codex 對話。
  房間之間訊息不互通，回覆只送回該房間綁定的群組。主人私訊 bot 歸到「主討論」房。
- 網頁上方用分頁切換房間；網頁打的字只進目前選的房間，並轉貼到該房間的群組。
- Telegram 只聽配對過的主人；網頁只綁 127.0.0.1，且需金鑰（secrets/web-key.txt）。
- 寫檔權限是全域設定；Claude 在可寫模式下用 auto 權限，安全相關動作要主人在該房間同意。
"""
import http.client
import glob
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
from deepseek_agent import DeepSeekAgent  # noqa: E402
from window_agent import WindowAgent  # noqa: E402

# 使用者 11-18 20:11：Gemini 搬到自己的 agy 視窗（官方 FAQ 不支援把 agy 包進應用程式）。
# True＝群裡叫 Gemini 只留單到 H:\Gemini用\群組\待回\；False＝回到舊的背景 agy -p。
GEMINI_IN_WINDOW = True


def make_gemini(room):
    if GEMINI_IN_WINDOW:
        return WindowAgent("Gemini", room.id, getattr(room, "name", ""))
    return rt.GeminiAgent("Gemini")
from secret_store import load_token  # noqa: E402
import terminal_bridge as tb  # noqa: E402
import shared_watch as sw  # noqa: E402

STATE = os.path.join(HERE, "tg_state.json")
LOG_DIR = os.path.join(HERE, "logs")
UI_FILE = os.path.join(HERE, "ui.html")
PORT = 8765
WEB_LIMIT = 150  # 網頁一次最多載入幾則（節省瀏覽器記憶體）
ACK_DELAY = 60  # 工作超過這麼多秒，才先貼出第一句話
MAIN = "main"
SHARED_ROOM_NAME = "機器人互相討論群"  # 共用區通知與 inbox 貼到這個房間（可在 tg_state 的 shared_room 指定 id）
SOLO_ROOMS = [("solo-nvidia", "我 vs 輝達", "nvidia"), ("solo-gemini", "我 vs Gemini", "gemini"),
              ("solo-dot", "我 vs Dot", "dot"), ("solo-deepseek", "我 vs DeepSeek", "deepseek")]
MAX_HOPS = 4  # 每則使用者訊息後，AI 之間用 @名字 互相點名最多接力幾次
ICON = {"使用者": "🧑", "Claude": "🟠", "Codex": "🟢", "Gemini": "🔵", "輝達": "🟩", "DeepSeek": "🐋", "系統": "⚙️"}
TG_HELP = ("直接說話：Claude、Codex 發表意見，Gemini 最後補充挑毛病（每個群組是獨立的討論房）\n"
           "/claude 問題、/codex 問題、/gemini 問題、/nvidia 問題、/deepseek 問題：只問一位（也可以開頭寫 @Gemini、@輝達、@DeepSeek）\n"
           "開頭連續點名＝串接：@Gemini @輝達 問題（依序回答）；@all 或 @大家＝四位都回\n/more 2：讓他們再討論 2 輪\n"
           "/note 文字：只記下，不叫他們（下次叫時會一起看到）\n"
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
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, http.client.HTTPException) as e:
            raise RuntimeError(f"Telegram {method} 連線失敗：{getattr(e, 'reason', e)}") from None
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} 失敗：{data.get('description')}")
        return data["result"]

    def send(self, chat_id, text):
        first = None
        for i in range(0, max(len(text), 1), 4000):
            r = self.call("sendMessage", chat_id=chat_id, text=text[i:i + 4000] or "（空白）")
            first = first or (r or {}).get("message_id")
        return first

    def edit(self, chat_id, message_id, text):
        self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4000])
        if len(text) > 4000:  # Telegram 單則上限 4096：超過的部分另發新訊息，不要截掉
            self.send(chat_id, text[4000:])


class Room:
    """一個獨立的討論房。"""

    def __init__(self, hub, rid, d):
        self.hub, self.id = hub, rid
        self.name = d.get("name") or "討論房"
        self.solo = d.get("solo")  # 一對一房：gemini／nvidia／dot（只有網頁，不連 Telegram）
        self.chats = list(d.get("chats") or [])
        self.transcript = list(d.get("transcript") or [])
        self.epoch = int(d.get("epoch") or 1)
        self.order = d.get("order") or "claude"
        self.approval = d.get("approval")
        self.log_name = d.get("log_name")
        self.claude, self.codex = rt.ClaudeAgent("Claude"), rt.CodexAgent("Codex")
        self.claude.session, self.claude.seen = d.get("claude_session"), int(d.get("claude_seen") or 0)
        self.codex.session, self.codex.seen = d.get("codex_session"), int(d.get("codex_seen") or 0)
        self.gemini = make_gemini(self)
        self.gemini.session, self.gemini.seen = d.get("gemini_session"), int(d.get("gemini_seen") or 0)
        self.nvidia = rt.NvidiaAgent("輝達")  # 沒有對話記憶，每次帶最近 60 則；只有點名才回
        self.deepseek = DeepSeekAgent("DeepSeek")  # 使用者 11-18 19:26：付費但便宜，Claude 管；只有點名才回
        # 終端機 Claude 讀到哪；第一次接手時只給最近幾則，舊紀錄需要時再去讀檔
        self.term_seen = int(d["claude_term_seen"]) if "claude_term_seen" in d else max(0, len(self.transcript) - 6)
        self.auto = int(d.get("auto") or 0)  # 兩位自動輪流回應的上限（每個話題）
        self.auto_left = 0
        self.stop_flag = False
        self.hops = 0

    def dump(self):
        return {"name": self.name, "chats": self.chats, "transcript": self.transcript, "epoch": self.epoch,
                "order": self.order, "approval": self.approval, "log_name": self.log_name,
                "claude_session": self.claude.session, "claude_seen": self.claude.seen,
                "codex_session": self.codex.session, "codex_seen": self.codex.seen,
                "gemini_session": self.gemini.session, "gemini_seen": self.gemini.seen,
                "claude_term_seen": self.term_seen, "auto": self.auto, "solo": self.solo}

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

    def tg_broadcast(self, text, track=None):
        for cid in self.targets():
            try:
                mid = self.hub.bot.send(cid, text)
                if track is not None and mid:
                    track[cid] = mid
            except Exception as e:
                log(f"[{self.name}] 轉發到 Telegram 失敗：{e}")

    def typing(self):
        for cid in self.targets():
            try:
                self.hub.bot.call("sendChatAction", chat_id=cid, action="typing")
            except Exception:
                pass

    # ---------- 紀錄 ----------
    def add(self, who, text, src, kind=None, track=None):
        """src: 電腦 / Telegram / 系統 / 回覆。非 Telegram 來源會轉貼到本房間的群組。
        kind="report" 是完成回報；track 傳 dict 時會記下各群組的 Telegram 訊息編號（之後可更新進度）。"""
        entry = {"who": who, "text": text, "src": src, "time": time.strftime("%m-%d %H:%M")}
        if kind:
            entry["kind"] = kind
        with self.hub.lock:
            self.transcript.append(entry)
            idx = len(self.transcript) - 1
            self.hub.save()
        if who != "Dot" and (re.search(r"(?m)^\s*@Dot(?![A-Za-z])|DOT_REQUEST", text or "", re.I)
                             or (who == "使用者" and kind != "note" and (self.solo == "dot" or (text or "").startswith("（只問 Dot）")))):
            notify_dot(self.name, who, entry["time"], text)  # Dot 不在線：留一張點名單，Dot 巡檢時看到再回
        if src != "Telegram":
            label = (("主人（電腦）" + ("・只記下" if kind == "note" else "")) if who == "使用者"
                     else who + ("（完成回報）" if kind == "report" else ""))
            tg_text = f"{ICON.get(who, '')} {label}\n\n{text}"
            if track is not None:
                track["_header"] = tg_text
                self.tg_broadcast(tg_text, track)  # 要拿到訊息編號，同步送
            else:
                threading.Thread(target=self.tg_broadcast, args=(tg_text,), daemon=True).start()
        return idx

    def notice(self, text):
        self.add("系統", text, "系統")

    # ---------- 狀態 ----------
    def agents(self):
        return (self.claude, self.codex, self.gemini, self.nvidia, self.deepseek)

    def order_both(self):
        return [self.claude, self.codex] if self.order == "claude" else [self.codex, self.claude]

    def order_all(self):
        """一般發言：Claude、Codex 先講，Gemini 最後（看得到前兩位的回答，負責補充與挑毛病）。
        互相討論群：只叫 Claude 當窗口，由它決定要不要 @ 別人（使用者 11-17：別一直叫醒快沒額度的 Codex）。
        Codex 週額度超速或快用完時，一般發言不叫它（點名還是會叫）。"""
        if self.solo in ("gemini", "nvidia", "deepseek"):  # 一對一房：只叫那一位
            return [getattr(self, self.solo)]
        if self.solo == "dot":  # Dot 不在線：發言會自動留點名單，不叫任何 AI
            return []
        if self.name == SHARED_ROOM_NAME:
            return [self.claude]
        seq = [a for a in self.order_both() if not (a is self.codex and codex_hot())]
        return seq + [self.gemini]

    def seq_for(self, target):
        return {"both": self.order_all(), "all": self.order_all(), "claude": [self.claude],
                "codex": [self.codex], "gemini": [self.gemini], "nvidia": [self.nvidia], "deepseek": [self.deepseek],
                "dot": []}.get(target, self.order_all())  # dot：不叫 AI，只留點名單給 Dot

    @property
    def busy(self):
        return any(a.busy for a in self.agents())

    @property
    def status(self):
        active = [a.name for a in self.agents() if a.busy]
        return "、".join(active) + " 工作中…" if active else "閒置"

    # ---------- 操作 ----------
    def note(self, text, src):
        """只記下：存進討論，不叫任何人。下次有人被叫時會一起看到。"""
        self.add("使用者", text, src, kind="note")

    def user_says(self, text, src, target="both", members=None):
        picked = []  # 開頭連續點名＝串接：「@Gemini @輝達 …」依序回答，後面的看得到前面的回答；@all／@大家＝四位
        if target == "both":  # 也接受開頭寫「（只問 Gemini）」
            while True:
                m = re.match(r"\s*(?:@|（只問\s*)(claude|codex|gemini|nvidia|輝達|deepseek|all|大家|全部)(?![A-Za-z])[）)]?[\s,，、]*",
                             text, re.I)
                if not m:
                    break
                k = m.group(1).lower()
                k = "nvidia" if k == "輝達" else "all" if k in ("大家", "全部") else k
                picked += ["claude", "codex", "gemini", "nvidia"] if k == "all" else [k]
                text = text[m.end():]
            picked = list(dict.fromkeys(picked))  # 去重，保留順序
            if not picked and members is not None:  # 網頁「問大家」的群組名單（下方按鈕旁的「群聊跳過」決定誰在名單裡）
                picked = [m for m in ("claude", "codex", "gemini", "nvidia", "deepseek", "dot") if m in members]
                # 使用者自己勾的名單照勾的叫（11-18 bug：勾了 Codex 卻被默默拿掉，只剩 Gemini 說話）；額度吃緊只提醒
                if "codex" in picked and codex_hot():
                    threading.Timer(1, lambda: self.notice("提醒：Codex 週額度吃緊，這次因為你勾了它還是會叫；"
                                                           "想省就把它切成「略」。")).start()
                if not picked:
                    picked = ["claude"]
            if len(picked) == 1:
                target = picked[0]
        names = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "nvidia": "輝達", "deepseek": "DeepSeek", "dot": "Dot"}
        if len(picked) > 1 and not (members is not None and picked in (["claude", "codex", "gemini"], ["claude", "gemini"])):
            # 標記請誰、照什麼順序，大家讀到記錄才不會搞錯（網頁預設名單就不加標記，免得每則都一長串）
            text = f"（請 {'→'.join(names[p] for p in picked)} 依序回答）{text}"
        elif target in names:  # 標記只問誰，其他人之後讀到記錄才不會搞錯
            text = f"（只問 {names[target]}）{text}"
        self.add("使用者", text, src)
        self.auto_left = self.auto
        self.hops = 0  # 使用者每說一次話，AI 之間的點名接力重新計數
        for a in self.agents():
            if a.busy and getattr(a, "report_progress", None):
                threading.Thread(target=a.report_progress, daemon=True).start()
        self.stop_flag = False
        if "dot" in picked and len(picked) > 1:
            notify_dot(self.name, "使用者", time.strftime("%m-%d %H:%M"), text)  # 群聊名單有 Dot：留點名單
        seq = [a for p in picked for a in self.seq_for(p)] if len(picked) > 1 else self.seq_for(target)
        for a in seq:
            if a.busy and not self.start_side(a):
                a.pending = True  # 分身也在忙：等手上的事做完再回
        self.chain([a for a in seq if not a.busy])

    # ---------- 分身：主線在忙時，先回答新問題（只讀、不搶主線的對話） ----------
    def start_side(self, agent):
        if agent.side is not None or agent in (self.gemini, self.nvidia, self.deepseek):  # Gemini 沒有分身：忙的話做完再回
            return False
        terminal = agent is self.claude and tb.terminal_alive()
        if not terminal and not agent.session:
            return False
        side = (rt.ClaudeAgent if agent is self.claude else rt.CodexAgent)(agent.name)
        side.fork_from, side.write = agent.session, False
        agent.side = side
        with self.hub.lock:
            snapshot = list(self.transcript)
        start = self.term_seen if terminal else agent.seen
        new = [m for m in snapshot[start:] if m["who"] != agent.name]
        body = "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in new)
        head = ("（你是分身：主線的你還在處理先前的工作。下面的新訊息，能直接回答的就回答；"
                "需要改檔、跑指令或要很久的，簡短說明「等手上的事做完再處理」，不要動手。"
                "不用回應主線正在做的那件事。）\n\n")

        def run():
            try:
                if terminal:
                    reply = tb.ask_terminal(self.name, f"（房間「{self.name}」｜主線工作中，這是插進來的新訊息）\n\n" + body,
                                            lambda: self.stop_flag)
                else:
                    reply = side.run(head + body + f"\n\n現在輪到你（{agent.name}）發言。")
                    reply = (side.said[-1] if side.said else reply or "").strip()
                if reply:
                    self.add(agent.name, reply, "回覆", kind="side")
                    log(f"[{self.name}] {agent.name} 分身先回答了新訊息")
                else:
                    agent.pending = True
            except Exception as e:
                log(f"[{self.name}] {agent.name} 分身失敗：{e}")
                agent.pending = True  # 分身不行，就等主線做完再回
            finally:
                agent.side = None
        threading.Thread(target=run, daemon=True).start()
        log(f"[{self.name}] {agent.name} 在忙，開分身先回答新訊息")
        return True

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
            self.gemini = make_gemini(self)
            self.nvidia = rt.NvidiaAgent("輝達")
            self.deepseek = DeepSeekAgent("DeepSeek")
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
                    seq = rest  # 已經在做了（例如自動輪流已叫起它），不重複
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

    def ask_terminal(self, agent, snapshot):
        """Claude 的回合先交給終端機那個 Claude Code；不在線或逾時回傳 None（改用備援）。"""
        if agent is not self.claude or not tb.terminal_alive():
            return None
        new = [m for m in snapshot[self.term_seen:] if m["who"] != "Claude"]
        if len(new) > rt.PROMPT_LIMIT:  # 太久沒輪到：只帶最近幾則，跟其他 AI 一樣
            skipped, new = len(new) - rt.PROMPT_LIMIT, new[-rt.PROMPT_LIMIT:]
            new.insert(0, {"who": "系統", "time": "", "text": f"（較早的 {skipped} 則省略；需要的話用 H:\\Claude用\\三方討論\\search.py 搜尋）"})
        body = "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in new) or "（沒有新發言，請針對目前討論補充或回應。）"
        perm = "可寫" if self.hub.state.get("write_mode") else "唯讀"
        where = "與 Telegram 群組" if self.targets() else "（這房間沒有連 Telegram）"
        body = (f"（房間「{self.name}」｜現在 {time.strftime('%m-%d %H:%M')}｜目前權限：{perm}｜回覆會以 Claude 身分貼到此房間的網頁{where}）\n\n"
                + body)
        agent.steps, agent.started = ["交給終端機的 Claude…"], time.time()
        log(f"[{self.name}] 交給終端機 Claude")

        def waiting(sec):
            agent.steps[-1:] = [f"等終端機的 Claude 回覆（{sec // 60}:{sec % 60:02d}）"]
        reply = tb.ask_terminal(self.name, body, lambda: self.stop_flag, waiting)
        if reply is not None:
            self.term_seen = len(snapshot)
        return reply

    def run_turn(self, agent):
        threading.Thread(target=self.typing, daemon=True).start()
        with self.hub.lock:
            snapshot = list(self.transcript)
        reply = self.ask_terminal(agent, snapshot)
        if reply is not None:
            agent.started = None
            self.add(agent.name, reply, "回覆")
            log(f"[{self.name}] 終端機 Claude 已回覆")
            self.auto_next(agent, reply)
            return
        if self.stop_flag:
            return
        if agent is self.claude and tb.terminal_alive():
            self.notice("終端機的 Claude 沒有及時回覆，這輪改由備援 Claude 回答。")
        agent.write = bool(self.hub.state.get("write_mode"))
        prompt = agent.build_prompt(snapshot)
        if agent.session is None and self.id != MAIN:
            prompt = f"（這是「{self.name}」討論房，和其他房間的討論互相獨立。）\n" + prompt
        log(f"[{self.name}] {agent.name} 開始工作…")
        t0 = time.time()
        src = "備援" if agent is self.claude else "回覆"
        # 每輪最多兩則：第一句話馬上貼出當「回覆」，中途的話只更新進度，做完再一次「完成回報」
        live = {"idx": None, "text": None, "last": None, "tg": {}, "done": False}

        def post_first():
            if live["idx"] is None and live["text"] and not live["done"]:
                live["idx"] = self.add(agent.name, live["text"], src, track=live["tg"])
                agent.anchor = live["idx"]

        def on_say(text):
            if live["text"] is None:
                live["text"] = text
                if time.time() - t0 >= ACK_DELAY:
                    post_first()
            else:
                live["last"] = text
            agent.step("說：" + text)
        agent.on_say = on_say

        def delayed_ack():  # 做很久才先貼第一句，讓主人知道在忙；快的話不用刻意報「收到」
            for _ in range(ACK_DELAY):
                if live["done"]:
                    return
                time.sleep(1)
            post_first()
        threading.Thread(target=delayed_ack, daemon=True).start()

        def tg_progress(final=None):
            header = live["tg"].get("_header")
            if not header:
                return
            if final:
                tail = final
            else:
                sec = int(time.time() - t0)
                tail = f"⏳ 還在處理這件：已 {sec // 60}:{sec % 60:02d}｜最新：{agent.steps[-1] if agent.steps else '…'}"
            for cid, mid in list(live["tg"].items()):
                if cid == "_header":
                    continue
                try:
                    self.hub.bot.edit(cid, mid, f"{header}\n\n{tail}")
                except Exception:
                    pass

        agent.report_progress = tg_progress  # 只有主人又傳新訊息時才更新上一件事的進度
        try:
            reply = agent.run(prompt)
        except Exception as e:
            live["done"] = True
            if live["idx"] is not None:
                agent.seen = len(snapshot)  # 已經說出口的話保留，不算白講
                tg_progress("⚠️ 這輪中途失敗")
            if not self.stop_flag:
                log(f"[{self.name}] {agent.name} 失敗：{e}")
                extra = f"\n最後說到：{live['last'][:300]}" if live["last"] else ""
                self.notice(f"{agent.name} 這輪中途失敗：{str(e)[:400]}{extra}")
            return
        finally:
            live["done"] = True
            agent.started = None
            agent.on_say = None
            agent.anchor = None
            agent.report_progress = None
        agent.seen = len(snapshot)  # 自己的發言會被過濾；途中新增的訊息下次看得到
        agent.extra_allow = []      # 允許一次：用完即清
        if live["idx"] is None:
            # 很快就做完（或沒有中途說話）：只貼一則，就是最後的結論
            final = (live["last"] or live["text"] or reply or "").strip()
            if final:
                self.add(agent.name, final, src)
        else:
            tg_progress(f"✅ 已完成（{int(time.time() - t0) // 60}:{int(time.time() - t0) % 60:02d}）")
            # 完成回報只取「最後說的那一段」；Codex 的 reply 是所有段落串起來的全文，不能整份再貼一次
            final = (live["last"] or "").strip()
            if final and final != (live["text"] or "").strip():
                self.add(agent.name, final, src, kind="report")
        log(f"[{self.name}] {agent.name} 完成（{time.time() - t0:.0f} 秒）")
        if getattr(agent, "denials", None):
            self.request_approval(agent)
        self.auto_next(agent, (live["last"] or live["text"] or reply or ""))

    def auto_next(self, agent, said):
        """自動輪流：這位說完，換另一位回應；有上限，也可以用【討論結束】提早結束。"""
        # AI 之間自己決定對話對象：發言裡「行首」寫 @名字 就叫那位回一次（不叫自己；每則使用者訊息最多接力 MAX_HOPS 次）
        names = {"claude": self.claude, "codex": self.codex, "gemini": self.gemini, "nvidia": self.nvidia,
                 "輝達": self.nvidia, "deepseek": self.deepseek}
        called = []
        for m in re.finditer(r"(?m)^\s*@(claude|codex|gemini|nvidia|輝達|deepseek)(?![A-Za-z])", said or "", re.I):
            a = names[m.group(1).lower()]
            if a is not agent and a not in called:
                called.append(a)
        if self.claude in called and claude_hot():  # 使用者 11-18：Claude 主持兩個 agent，額度要留著；AI 點名不叫醒 Claude
            called.remove(self.claude)
            self.notice("Claude 的 5 小時額度吃緊，AI 之間點名 @Claude 先不叫醒（使用者直接問照常）；請把需求寫清楚，額度恢復後處理。")
        if called and not self.stop_flag:
            if self.hops >= MAX_HOPS:
                self.notice(f"AI 之間的接力已達上限（{MAX_HOPS} 次），等使用者下一則訊息再繼續。")
            else:
                self.hops += 1
                log(f"[{self.name}] {agent.name} 點名：{'、'.join(a.name for a in called)}")
                todo = [a for a in called if not a.busy]
                for a in called:
                    if a.busy:
                        a.pending = True
                threading.Thread(target=lambda q=todo: self.chain(q), daemon=True).start()
        if self.auto <= 0 or self.auto_left <= 0 or self.stop_flag or not (said or "").strip():
            return
        if agent in (self.gemini, self.nvidia, self.deepseek):  # 自動輪流只在 Claude、Codex 之間；Gemini 說完不再接力
            return
        if "【討論結束】" in said:
            self.auto_left = 0
            return
        self.auto_left -= 1
        other = self.codex if agent is self.claude else self.claude
        if other.busy or (other is self.codex and codex_hot()):  # Codex 額度吃緊時不自動輪到它
            return
        log(f"[{self.name}] 自動輪流：換 {other.name} 回應（剩 {self.auto_left} 輪）")
        threading.Thread(target=lambda: self.chain([other]), daemon=True).start()

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
        if start == 0:  # 剛打開或切房：網頁只載入最近 WEB_LIMIT 則，舊的看 logs/*.md
            start = max(0, len(t) - WEB_LIMIT)
        return {"room": self.id, "epoch": self.epoch, "reset": reset, "start": start, "messages": t[start:],
                "total": len(t), "busy": self.busy, "status": self.status, "order": self.order,
                "approval": self.approval,
                "progress": [{"who": a.name, "steps": list(a.steps), "pending": a.pending,
                              "anchor": getattr(a, "anchor", None),
                              "elapsed": int(time.time() - (a.started or time.time()))}
                             for a in self.agents() if a.busy]
                            + [{"who": a.name, "label": f"{a.name}（分身）", "steps": list(a.side.steps), "pending": False,
                                "anchor": None, "elapsed": int(time.time() - (a.side.started or time.time()))}
                               for a in self.agents() if getattr(a, "side", None) is not None]}  # 分身在回答也要顯示「說話中」


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
        self.seen_posts = []
        agent_keys()  # 先建好視窗 agent 的金鑰檔（Gemini 的放在 H:\Gemini用\.群組金鑰.txt）
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
        for rid, name, solo in SOLO_ROOMS:  # 使用者 11-17：我 vs 輝達／Gemini／Dot 的一對一視窗，不連 Telegram
            s["rooms"].setdefault(rid, {"name": name, "chats": [], "solo": solo})
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

    # ---------- 共用區 H:\Agent共用：新檔通知＋inbox 發言（不呼叫模型） ----------
    def shared_room(self):
        rid = self.state.get("shared_room") or next(
            (r.id for r in self.rooms.values() if r.name == SHARED_ROOM_NAME), None)
        return self.rooms.get(rid)

    def shared_loop(self):
        seen_path = os.path.join(HERE, "shared_seen.json")
        prev = sw.load_seen(seen_path)
        first = prev is None
        throttle = sw.Throttle()  # 通知規則見 H:\Agent共用\通知規則.txt
        while True:
            try:
                snap, added, changed = sw.scan(prev or {})
                if first:
                    log(f"共用區基準：{len(snap)} 個檔（第一次掃描不通知）")
                    first = False
                else:
                    throttle.feed(added, changed)
                    batch = throttle.ready()
                    if batch and self.shared_room():
                        self.shared_room().notice("共用區有變動：\n" + sw.summarize(*batch))
                sw.save_seen(seen_path, snap)
                prev = snap
                for path, sender, to, body, want_room in sw.read_inbox():
                    room = next((r for r in self.rooms.values() if want_room and want_room in (r.id, r.name)),
                                None) or self.shared_room()  # 檔頭可寫 room: 指定房間（例如 我 vs Dot）
                    if room is None:
                        break
                    if to == "invalid":
                        room.notice(f"inbox 收到格式不對的檔（{os.path.basename(path)}），已移到 inbox/rejected/。"
                                    "開頭要有 from:、to:（claude／codex／both／none）、再一行 ---")
                        sw.archive(path, ok=False)
                        continue
                    log(f"inbox：{sender} → {to}")
                    room.add(sender, body, "共用區")
                    sw.archive(path)
                    if to != "none":
                        room.auto_left = room.auto
                        room.stop_flag = False
                        seq = room.seq_for(to)
                        threading.Thread(target=lambda q=seq, r=room: r.chain(q), daemon=True).start()
            except Exception as e:
                log(f"共用區監看錯誤：{e}")
            time.sleep(30)

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
            except Exception as e:  # 包含 Telegram 斷線（RemoteDisconnected 等）：不能讓整個中樞當掉
                log(f"Telegram 讀取失敗，5 秒後重試：{e}")
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
        elif word in ("/claude", "/codex", "/gemini", "/nvidia", "/deepseek"):
            if arg:
                room.user_says(arg, "Telegram", word[1:])
        elif word == "/auto":
            n = int(arg) if arg.isdigit() else None
            if n is None:
                self.bot.send(cid, f"⚙️ 這個房間自動輪流上限：{room.auto} 輪（0 表示只有被點名才回）。用 /auto 3 設定。")
            else:
                with self.lock:
                    room.auto = max(0, min(10, n))
                    self.save()
                self.bot.send(cid, f"⚙️ 這個房間改成自動輪流最多 {room.auto} 輪。")
        elif word == "/note":
            if arg:
                room.note(arg, "Telegram")
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
                           "approval": bool(x.approval), "linked": bool(x.targets()), "solo": x.solo}
                          for x in self.rooms.values()]
            d["write"] = bool(self.state.get("write_mode"))
            d["health"] = {k: {"ok": v[0], "msg": v[1]} for k, v in self.health.items()}
            d["terminal"] = tb.terminal_alive()
            try:  # 網頁版本：ui.html 改過就變，頁面比對後顯示「有更新」按鈕
                d["ui_ver"] = int(os.path.getmtime(UI_FILE))
            except OSError:
                d["ui_ver"] = 0
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

        def _agent(self):
            """視窗裡的 agent（目前只有 Gemini）用自己的低權限金鑰：只能讀房間、以自己名義發言（使用者 11-18 20:11 移交）。"""
            k = self.headers.get("X-Agent-Key") or ""
            for name, ak in agent_keys().items():
                if k and secrets.compare_digest(k, ak):
                    return name
            return None

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
                if not (self._authed() or self._agent()):
                    return self._json({"error": "forbidden"}, 403)
                self._json(hub.snapshot(q.get("room", [MAIN])[0], int(q.get("since", ["0"])[0]),
                                        int(q.get("epoch", ["0"])[0])))
            elif u.path == "/api/usage":
                if not self._authed():
                    return self._json({"error": "forbidden"}, 403)
                self._json(usage_summary())
            elif u.path == "/ping":
                self._json({"app": "roundtable-hub"})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            agent = None if self._authed() else self._agent()
            if not self._authed() and not (agent and urlparse(self.path).path == "/api/agent_says"):
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
                if text and data.get("target") == "note":
                    room.note(text, "電腦")
                elif text:
                    mem = data.get("members")
                    room.user_says(text, "電腦", data.get("target") if data.get("target") in ("claude", "codex", "gemini", "nvidia", "deepseek", "dot") else "both",
                                   members=[m for m in mem if isinstance(m, str)] if isinstance(mem, list) else None)
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
            if p in ("/api/claude_says", "/api/agent_says"):
                # 終端機的 Claude 或 App 裡的 Codex 以自己的身分在房間發言，可指定接著由誰回應
                text = (data.get("text") or "").strip()
                who = agent or ("Codex" if (p == "/api/agent_says" and data.get("who") == "Codex") else "Claude")
                if not text:
                    return self._json({"ok": False})
                cid = str(data.get("client_id") or "")[:64]
                if cid:  # 逾時重送不重複貼（Codex 11-18 20:20）
                    if cid in hub.seen_posts:
                        return self._json({"ok": True, "duplicate": True})
                    hub.seen_posts.append(cid)
                    del hub.seen_posts[:-200]
                room.add(who, text, {"Codex": "App", "Gemini": "視窗"}.get(who, "回覆"))
                if agent:  # 視窗 agent 行首 @誰 照常點名（有接力上限）；它自己不會被叫
                    threading.Thread(target=room.auto_next, args=(getattr(room, agent.lower()), text), daemon=True).start()
                room.auto_left = room.auto
                with hub.lock:
                    if who == "Claude":
                        room.term_seen = len(room.transcript)
                    hub.save()
                nxt = data.get("then")
                if nxt in ("codex", "claude", "gemini", "nvidia", "deepseek", "both") and not agent:
                    room.stop_flag = False
                    room.chain(room.seq_for(nxt))
                return self._json({"ok": True})
            if p == "/api/write":
                return self._json({"write": hub.set_write(data.get("on"))})
            if p == "/api/order":
                return self._json({"order": room.toggle_order()})
            self._json({"error": "not found"}, 404)

    return H


# ---------- 自主工作時段（使用者 11-17：讓輝達、Gemini 像正常 agent 一樣自己持續做事） ----------
AUTO_AGENTS = [  # (房間 id, agent 屬性, 目標檔, 間隔秒)
    ("solo-nvidia", "nvidia", r"H:\免費模型用\目標.md", 30 * 60),
    # Gemini 搬到視窗後自己用 /schedule 醒來，中樞不再給它自主時段（11-18 20:4x）
]
AUTO_PROMPT = ("⏰ 自主工作時段：請讀你的目標檔 {goal}，做「下一步」→ 驗證（能跑程式就跑、能讀檔核對就核對）→ "
               "更新目標檔（狀態、下一步、完成標準、需要誰驗證）→ 在這裡用幾行回報做了什麼、驗證結果、下一步。"
               "沒驗證的不要說成結論；全部完成或卡住就把目標檔第一行改成「狀態：暫停」並說明原因；需要權限或執行環境才 @Claude。")


def _work_memory(name, mode):
    """工作記憶（主項、自己的待辦、專案索引）：規則在 H:\Agent共用\記憶\規則.json（使用者 11-18 11:27）。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("注入", r"H:\Agent共用\記憶\注入.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m.build(name, mode)
    except Exception:
        return ""


def _auto_pace(kind, base_gap):
    """自主時段動態降速：回傳 (是否執行, 建議間隔秒, 步數上限)。接近額度先拉長間隔與減步數，滿載才停。"""
    try:
        if kind == "nvidia":
            import datetime
            import nvidia_agent as na
            b = json.load(open(json.load(open(r"H:\Agent共用\ai-call\paths.json", encoding="utf-8"))["budget"],
                               encoding="utf-8"))
            now = datetime.datetime.now(datetime.timezone.utc)
            all_used = b.get("attempts", 0) if b.get("day") == now.date().isoformat() else 0
            auto_used = na._today_calls("auto")
            frac = max((now.hour * 3600 + now.minute * 60) / 86400, 0.05)
            if all_used >= 900 * frac * 0.9 or auto_used >= na.AUTO_DAILY:
                return False, base_gap, 0
            ratio = max(all_used / (900 * 0.8 * frac), auto_used / (na.AUTO_DAILY * frac))
            if ratio > 1.15:
                return True, min(240 * 60, int(base_gap * 2.5)), 4
            if ratio > 0.85:
                return True, min(240 * 60, int(base_gap * 1.5)), 8
            return True, base_gap, 12
        u = json.load(open(r"H:\Agent共用\gemini-用量.json", encoding="utf-8"))
        h5, wk = u.get("five_hour") or {}, u.get("seven_day") or {}
        h5_u, wk_u = float(h5.get("used") or 0), float(wk.get("used") or 0)
        h5_st, wk_st = h5.get("state") or "", wk.get("state") or ""
        if h5_u >= 80 or wk_u >= 85:
            return False, base_gap, 0
        if h5_u >= 55 or wk_u >= 70 or wk_st == "超速" or h5_st == "超速":
            return True, min(240 * 60, int(base_gap * 2)), 0
        if h5_u >= 40 or wk_st == "略快" or h5_st == "略快":
            return True, min(240 * 60, int(base_gap * 1.5)), 0
        return True, base_gap, 0
    except Exception:
        return False, base_gap, 0


def auto_loop(hub):
    last = {}
    time.sleep(120)
    while True:
        for rid, attr, goal, every in AUTO_AGENTS:
            try:
                room = hub.rooms.get(rid)
                if not room or not os.path.exists(goal):
                    continue
                head = open(goal, encoding="utf-8", errors="replace").read(400)
                if "狀態：進行中" not in head and "狀態:進行中" not in head:
                    continue
                m = re.search(r"喚醒[間隔]*[：:]\s*(\d+)\s*分", head)  # agent 自己決定多久叫一次（10–240 分鐘）
                base_gap = max(10, min(240, int(m.group(1)))) * 60 if m else every
                ok, gap, auto_steps = _auto_pace(attr, base_gap)
                if not ok or time.time() - last.get(rid, 0) < gap:
                    continue
                agent = getattr(room, attr)
                if room.busy or agent.busy:
                    continue
                if attr == "nvidia":
                    agent._auto_steps = auto_steps
                last[rid] = time.time()
                room.stop_flag = False
                room.notice(AUTO_PROMPT.format(goal=goal) + _work_memory(agent.name, "自主"))
                log(f"[{room.name}] 自主工作時段：叫 {agent.name}")
                threading.Thread(target=lambda r=room, a=agent: r.chain([a]), daemon=True).start()
            except Exception as e:
                log(f"自主工作時段錯誤（{rid}）：{e}")
        time.sleep(60)


DOT_DIR = r"H:\Agent共用\給Dot"


def notify_dot(room, who, when, text):
    """房間裡有人寫行首 @Dot 或 DOT_REQUEST：在 給Dot\\待處理\\ 留一張點名單（只放請求那段，最多 1500 字）。
    Dot 回覆走既有 inbox（from: Dot），會以 Dot 的名字貼回機器人互相討論群。"""
    try:
        d = os.path.join(DOT_DIR, "待處理")
        os.makedirs(d, exist_ok=True)
        name = time.strftime("%m%d-%H%M%S-") + re.sub(r"[^\w]", "", who)[:12] + ".txt"
        tmp = os.path.join(d, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"房間：{room}\n誰點名：{who}\n時間：{when}\n---\n{(text or '')[:1500]}\n")
        os.replace(tmp, os.path.join(d, name))
    except OSError as e:
        log(f"給 Dot 的點名單寫入失敗：{e}")


def claude_hot():
    """Claude（終端機主持人）5 小時用量 ≥75% 或速度 >1.5、或週用量 ≥95%：AI 之間的點名不叫醒它，留額度給使用者。"""
    try:
        d = json.load(open(r"H:\Agent共用\claude-用量.json", encoding="utf-8"))
        h5, wk = d.get("five_hour") or {}, d.get("seven_day") or {}
        return (h5.get("used", 0) >= 75 or (h5.get("speed") or 0) > 1.5 and h5.get("used", 0) >= 50
                or wk.get("used", 0) >= 95)
    except Exception:
        return False


_codex_hot_cache = {"t": 0, "v": False}


def codex_hot():
    """Codex 週額度超速或用了 85% 以上 → True（一般發言和自動輪流就不叫它；點名仍會叫）。讀檔結果快取 60 秒。"""
    if time.time() - _codex_hot_cache["t"] < 60:
        return _codex_hot_cache["v"]
    v = False
    try:
        w = json.load(open(r"H:\Agent共用\codex-用量.json", encoding="utf-8")).get("seven_day") or {}
        v = float(w.get("used") or 0) >= 98  # 使用者 11-18：Codex 是 Pro，96% 還能說話；只有幾乎用完才在群聊自動略過
    except Exception:
        pass
    _codex_hot_cache.update(t=time.time(), v=v)
    return v


def _usage_window(w, label=None):
    """把各家用量檔的一個時間窗整理成網頁要的格式；沒資料回傳 None。"""
    if not w or w.get("used") is None or w.get("available") is False:
        return None
    resets = str(w.get("resets") or "")
    if "T" in resets:  # ISO 時間 → MM-DD HH:MM
        resets = resets[5:16].replace("T", " ")
    empty = str(w.get("empty_at") or "")
    if "T" in empty:
        empty = empty[5:16].replace("T", " ")
    return {"label": label or w.get("label"), "used": float(w["used"]), "elapsed": w.get("elapsed"),
            "state": w.get("state") or "", "resets": resets, "empty_at": empty}


def usage_summary():
    """網頁側邊用量表：Claude、Codex、Gemini 讀共用區的用量檔；輝達讀 OpenRouter worker 的當日計數。"""
    out = []
    for name, fn in (("Claude", "claude-用量.json"), ("Codex", "codex-用量.json"), ("Gemini", "gemini-用量.json")):
        p = os.path.join(r"H:\Agent共用", fn)
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            out.append({"name": name, "missing": True})
            continue
        wins = [x for x in (_usage_window(d.get("five_hour"), "5小時"), _usage_window(d.get("seven_day"), "7天")) if x]
        if name == "Gemini":
            argon = ((d.get("其他組") or {}).get("Gemini 4 Argon") or {})
            for v in argon.values():
                x = _usage_window(v, "Argon 週")
                if x:
                    if not x["used"] and not x.get("elapsed"):  # 還沒用過：重置時間只是「現在＋7 天」的佔位，別讓人誤會
                        x.update(label="Argon 週（未用）", resets="", state="")
                    wins.append(x)
        upd = str(d.get("更新") or "")
        verdict = d.get("判斷") or ""
        if name == "Gemini" and verdict:  # 判斷是看 Hydrogen／Flash 共用池，不是 Argon（使用者 11-18 16:4x 以為顯示有 bug）
            verdict = "Hydrogen／Flash 池：" + verdict
        out.append({"name": name, "windows": wins, "verdict": verdict,
                    "updated": upd[5:16].replace("T", " ") if len(upd) >= 16 else upd,
                    "age_min": int((time.time() - os.path.getmtime(p)) / 60)})
    try:  # 輝達：worker 的 UTC 日計數（台北 08:00 換日），上限 900（已扣保留）
        import datetime
        paths = json.load(open(r"H:\Agent共用\ai-call\paths.json", encoding="utf-8"))
        b = json.load(open(paths.get("budget", r"D:\Codex用\work\openrouter-worker\budget.json"), encoding="utf-8"))
        now = datetime.datetime.now(datetime.timezone.utc)
        used = b.get("attempts", 0) if b.get("day") == now.date().isoformat() else 0
        elapsed = (now.hour * 3600 + now.minute * 60 + now.second) / 864
        pct = used / 9
        state = "正常" if elapsed < 3 or pct <= elapsed else "略快" if pct <= elapsed * 1.2 else "超速"
        out.append({"name": "輝達", "windows": [{"label": "今天", "used": round(pct, 1), "elapsed": round(elapsed, 1),
                                                "state": state, "resets": "08:00", "empty_at": "",
                                                "count": f"{used}/900"}],
                    "verdict": f"今天 {used}/900 次（台北 08:00 換日）", "updated": time.strftime("%m-%d %H:%M"), "age_min": 0})
    except Exception:
        out.append({"name": "輝達", "missing": True})
    try:  # DeepSeek：付費，看本機花費帳（每日／每月上限在 H:\Agent共用i-call\deepseek\設定.json）
        import deepseek_agent as dsa
        ds = dsa._ds()
        d, m = ds.spent()
        c = ds.conf()
        dl, ml = c["每日上限美元"], c["每月上限美元"]
        out.append({"name": "DeepSeek", "windows": [
            {"label": "今天", "used": round(100 * d / dl, 1), "elapsed": None, "state": "", "resets": "00:00", "empty_at": ""},
            {"label": "本月", "used": round(100 * m / ml, 1), "elapsed": None, "state": "", "resets": "", "empty_at": ""}],
            "verdict": f"今天 ${d:.3f}／${dl}，本月 ${m:.3f}／${ml}", "updated": time.strftime("%m-%d %H:%M"), "age_min": 0})
    except Exception:
        out.append({"name": "DeepSeek", "missing": True})
    return {"agents": out, "time": time.strftime("%H:%M")}


def agent_keys():
    """各視窗 agent 的低權限金鑰：secrets/agent-key-<名字>.txt；同一份也放在該 agent 的家（例如 H:\Gemini用\.群組金鑰.txt）。"""
    out = {}
    for name, home in (("Gemini", r"H:\Gemini用"),):
        p = os.path.join(HERE, "secrets", f"agent-key-{name}.txt")
        try:
            out[name] = open(p, encoding="utf-8").read().strip()
        except OSError:
            k = secrets.token_urlsafe(24)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(k)
            out[name] = k
        try:
            hp = os.path.join(home, ".群組金鑰.txt")
            if not os.path.exists(hp) or open(hp, encoding="utf-8").read().strip() != out[name]:
                open(hp, "w", encoding="utf-8").write(out[name])
        except OSError:
            pass
    return out


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
    if sys.stdout and sys.stdout.isatty():  # 由看門狗無視窗執行時不要叫 cmd，免得跳出黑視窗
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

    threading.Thread(target=hub.shared_loop, daemon=True).start()
    threading.Thread(target=auto_loop, args=(hub,), daemon=True).start()

    def run_check():
        log("自我檢查中（Claude 回話、Codex 執行指令）…")
        hub.health = rt.self_check()
        for k, (ok, msg) in hub.health.items():
            log(f"自我檢查 {k}：{'正常' if ok else '有問題 → ' + msg}")
    threading.Thread(target=run_check, daemon=True).start()
    # 中樞在等終端機 Claude 時當掉或重開：Claude 已經寫好的回覆還在 outbox，補貼回原房間（11-17 22:25 遺失事件）
    for jf in glob.glob(os.path.join(HERE, "terminal", "inbox", "*.json")):
        rid = os.path.basename(jf)[:-5]
        out = os.path.join(HERE, "terminal", "outbox", rid + ".txt")
        try:
            req = json.load(open(jf, encoding="utf-8"))
            r = next((x for x in hub.rooms.values() if x.name == req.get("room")), None)
            if os.path.exists(out) and r:
                r.add("Claude", open(out, encoding="utf-8").read().strip() + "\n\n（中樞重開前沒送出，補貼）", "回覆")
                os.replace(out, out + ".done")
                log(f"[{r.name}] 補貼終端機 Claude 的回覆 {rid}")
            os.replace(jf, os.path.join(HERE, "terminal", "inbox", rid + ".handled"))
        except Exception as e:
            log(f"補貼終端機回覆失敗 {rid}：{e}")
    for r in hub.rooms.values():  # 上次中斷或失敗、還沒回答的訊息：請沒回到的那位補答
        if r.solo:  # 一對一房不自動補答（Gemini／輝達／Dot 都不在重開時補）
            continue
        t = r.transcript
        last_user = max((i for i, m in enumerate(t) if m["who"] == "使用者" and m.get("kind") != "note"), default=None)
        if last_user is None:
            continue
        text = t[last_user]["text"]
        wanted = ([r.claude] if text.startswith("（只問 Claude）") else
                  [r.codex] if text.startswith("（只問 Codex）") else
                  [] if text.startswith(("（只問 Gemini）", "（只問 輝達）", "（只問 Dot）", "（請 ")) else
                  [a for a in r.order_all() if a in (r.claude, r.codex)])  # 重開補答照房間規則（互相討論群只叫 Claude、Codex 吃緊不叫）
        answered = {m["who"] for m in t[last_user + 1:] if m["who"] in ("Claude", "Codex")}
        todo = [a for a in wanted if a.name not in answered]
        if todo:
            log(f"[{r.name}] 有訊息還沒回答到，請 {'、'.join(a.name for a in todo)} 補答。")
            r.chain(todo)
    hub.tg_loop()
