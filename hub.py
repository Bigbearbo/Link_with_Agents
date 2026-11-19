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
import shutil
import subprocess
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


APP_POSTERS = ("視窗Gemini", "Dot")  # 使用者 11-19：App 裡的 AI 經雲端硬碟「發言」資料夾貼到群組（drive_post.py 代送）
NVIDIA_IN_WINDOW = True  # 使用者 11-18 21:2x：輝達也搬家，住 OpenCode 視窗（H:\免費模型用）；False＝回到舊的工具迴圈


def make_nvidia(room):
    if NVIDIA_IN_WINDOW:
        return WindowAgent("輝達", room.id, getattr(room, "name", ""))
    return rt.NvidiaAgent("輝達")
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
        self.nvidia = make_nvidia(self)  # 只有點名才回
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
        if src == "Telegram" and target == "both":
            text, members = tg_members(self, text)          # 使用者 11-19 08:47：Telegram 用設定檔決定誰回、誰先回
            if text is None:
                return
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
                picked = [m for m in members if m in ("claude", "codex", "gemini", "nvidia", "deepseek", "dot")]  # 照名單順序（Telegram「先回」排第一）
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
            self.nvidia = make_nvidia(self)
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

    def auto_next(self, agent, said, poster=None):
        """自動輪流：這位說完，換另一位回應；有上限，也可以用【討論結束】提早結束。
        poster＝App 裡的 AI 名稱（agent 是 None）：只處理 @點名，不自動輪流。"""
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
                log(f"[{self.name}] {poster or agent.name} 點名：{'、'.join(a.name for a in called)}")
                todo = [a for a in called if not a.busy]
                for a in called:
                    if a.busy:
                        a.pending = True
                threading.Thread(target=lambda q=todo: self.chain(q), daemon=True).start()
        if poster or self.auto <= 0 or self.auto_left <= 0 or self.stop_flag or not (said or "").strip():
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
            if isinstance(d, dict) and d.get("kind") == "寫入":  # DeepSeek 等：想寫共用區的某個資料夾
                text, rule = f"寫入資料夾 {d['dir']}", ["寫入", d["dir"]]
            else:
                text, rule = rt.describe_denial(d)
            if rule not in rules:
                items.append(text)
                rules.append(rule)
        agent.denials = []
        with self.hub.lock:
            # DeepSeek 03:10：只有專案裡的資料夾可以「以後都允許」；共用區根目錄、課程等全隊設定一律每次問
            proj = os.path.realpath(r"H:\Agent共用\專案").lower() + os.sep
            always_ok = all(isinstance(r, list) and r[:1] == ["寫入"] and os.path.realpath(r[1]).lower().startswith(proj)
                            for r in rules)
            self.approval = {"agent": agent.name, "items": items, "rules": rules, "id": secrets.token_hex(4),
                             "always_ok": always_ok}
            self.hub.save()
        body = "\n".join(f"• {t}" for t in items)
        self.notice(f"🔐 {agent.name} 請求同意：\n{body}\n\n允許一次請按「允許一次」或傳 /allow"
                    + ("；這個資料夾以後都可以寫，請按「以後都允許」或傳 /always" if always_ok else "")
                    + "；拒絕請按「拒絕」或傳 /deny。")

    def answer_approval(self, allow, src, always=False):
        with self.hub.lock:
            ap, self.approval = self.approval, None
            self.hub.save()
        if not ap:
            return False
        agent = {a.name: a for a in self.agents()}.get(ap["agent"]) or (self.claude if ap["agent"] == "Claude" else self.codex)
        items = "、".join(ap["items"])
        writes = [r[1] for r in ap["rules"] if isinstance(r, list) and r[:1] == ["寫入"]]
        if allow and writes:
            if always and ap.get("always_ok"):
                grant_write(agent.name, writes, self.name)  # 使用者 11-19 03:07：「允許一次，以後這類都行」
                self.add("使用者", f"（只問 {agent.name}）以後這些資料夾你都可以寫：{items}。請繼續剛才的工作。", src)
            else:
                agent.next_extra_write = writes
                self.add("使用者", f"（只問 {agent.name}）我允許你這一次：{items}。請繼續剛才的工作。", src)
        elif allow:
            agent.extra_allow = list(ap["rules"])
            self.add("使用者", f"（只問 {agent.name}）我允許你這一次執行：{items}。請繼續剛才的工作。", src)
        else:
            self.add("使用者", f"（只問 {agent.name}）我不允許：{items}。請改用別的做法，或說明還需要什麼。", src)
        perm_log({"房間": self.name, "請求者": agent.name, "項目": ap["items"],
                  "動作": ("以後都允許" if (allow and always and ap.get("always_ok")) else "允許一次" if allow else "拒絕"),
                  "批准者": "使用者", "來源": src})
        self.stop_flag = False
        self.chain([agent])
        return True

    # ---------- 住視窗的 agent 回報狀態（使用者 11-19：像以前一樣顯示「工作中」，也能說一句進度） ----------
    WINDOW_STALE = 900  # 15 分鐘沒更新就當作已結束，免得視窗關掉後一直顯示

    def set_window_status(self, name, text=None, done=False):
        ws = self.__dict__.setdefault("window_status", {})
        if done:
            ws.pop(name, None)
            return
        now = time.time()
        s = ws.get(name)
        if not s or now - s["updated"] > self.WINDOW_STALE:
            s = ws[name] = {"steps": [], "started": now, "updated": now}
        s["updated"] = now
        if text and (not s["steps"] or s["steps"][-1] != text):
            s["steps"] = (s["steps"] + [text[:120]])[-8:]

    def window_progress(self):
        now, out = time.time(), []
        for name, s in list(getattr(self, "window_status", {}).items()):
            if now - s["updated"] > self.WINDOW_STALE:
                self.window_status.pop(name, None)
                continue
            out.append({"who": name, "steps": list(s["steps"]), "pending": False, "anchor": None,
                        "elapsed": int(now - s["started"])})
        return out

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
                            + self.window_progress()
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
        elif word in ("/allow", "/deny", "/always"):
            if not room.answer_approval(word != "/deny", "Telegram", word == "/always"):
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
            elif u.path == "/待討論" or u.path == "/decisions":
                if not self._authed(q):
                    return self._json({"error": "請用啟動程式開啟的網址"}, 403)
                body = decisions_page(q.get("k", [""])[0]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/ping":
                self._json({"app": "roundtable-hub"})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            agent = None if self._authed() else self._agent()
            if not self._authed() and not (agent and urlparse(self.path).path in ("/api/agent_says", "/api/agent_status")):
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
                return self._json({"ok": room.answer_approval(bool(data.get("allow")), "電腦", bool(data.get("always")))})
            if p == "/api/decide":
                # 待討論頁的留言（使用者 11-19 08:05）：記進待辦紀錄，並以使用者身分貼到主討論讓大家看到
                tid, text = int(data.get("id") or 0), (data.get("text") or "").strip()
                if not tid or not text:
                    return self._json({"ok": False})
                r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-X", "utf8", r"H:\Agent共用\待辦\待辦.py",
                                    "做", str(tid), "使用者回覆：" + text[:500]], capture_output=True, timeout=60,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
                hub.room(MAIN).user_says(f"（待討論 #{tid} 的回覆）{text}", "電腦", "claude")
                return self._json({"ok": r.returncode == 0})
            if p == "/api/agent_status":
                # 視窗 agent：{"room", "text": "正在讀單…"} 顯示工作中＋這一步；{"done": true} 收掉
                who = agent or (data.get("who") if data.get("who") in ("Gemini", "輝達") else None)
                if not who:
                    return self._json({"ok": False})
                room.set_window_status(who, (data.get("text") or "").strip() or None, bool(data.get("done")))
                return self._json({"ok": True})
            if p in ("/api/claude_says", "/api/agent_says"):
                # 終端機的 Claude 或 App 裡的 Codex 以自己的身分在房間發言，可指定接著由誰回應
                text = (data.get("text") or "").strip()
                # 使用者 11-19 01:4x：App 裡的 AI（視窗Gemini、Dot）從雲端硬碟「發言」資料夾貼文，由本機 drive_post.py 代送
                app_ai = data.get("who") if (p == "/api/agent_says" and not agent and data.get("who") in APP_POSTERS) else None
                who = agent or app_ai or ("Codex" if (p == "/api/agent_says" and data.get("who") == "Codex") else "Claude")
                if not text:
                    return self._json({"ok": False})
                cid = str(data.get("client_id") or "")[:64]
                if cid:  # 逾時重送不重複貼（Codex 11-18 20:20）
                    if cid in hub.seen_posts:
                        return self._json({"ok": True, "duplicate": True})
                    hub.seen_posts.append(cid)
                    del hub.seen_posts[:-200]
                room.add(who, text, {"Codex": "App", "Gemini": "agy"}.get(who, "雲端硬碟" if app_ai else "回覆"))
                if app_ai:  # 只認行首 @點名（有接力上限），不觸發 Claude／Codex 自動輪流
                    threading.Thread(target=room.auto_next, args=(None, text, app_ai), daemon=True).start()
                if agent:  # 視窗 agent 行首 @誰 照常點名（有接力上限）；它自己不會被叫
                    close_ticket(agent, room.id)  # Gemini 03:31：它在這房間發言了＝這張待回單由中樞銷掉，不靠它手動移
                    threading.Thread(target=room.auto_next, args=({"Gemini": room.gemini, "輝達": room.nvidia}[agent], text), daemon=True).start()
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
    # 輝達 11-18 21:2x 搬進 OpenCode 視窗，中樞不再給自主時段（要定時就在視窗裡排）
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
        import window_agent as _wa
        used, _w, oc = _wa.nvidia_total_today() or (b.get("attempts", 0) if b.get("day") == now.date().isoformat() else 0, 0, 0)
        elapsed = (now.hour * 3600 + now.minute * 60 + now.second) / 864
        pct = used / 9
        state = "正常" if elapsed < 3 or pct <= elapsed else "略快" if pct <= elapsed * 1.2 else "超速"
        out.append({"name": "輝達", "windows": [{"label": "今天", "used": round(pct, 1), "elapsed": round(elapsed, 1),
                                                "state": state, "resets": "08:00", "empty_at": "",
                                                "count": f"{used}/900"}],
                    "verdict": f"今天 {used}/900 次（OpenCode 實數，對得上 OpenRouter 網站；台北 08:00 換日）", "updated": time.strftime("%m-%d %H:%M"), "age_min": 0})
    except Exception:
        out.append({"name": "輝達", "missing": True})
    try:  # DeepSeek：付費，看本機花費帳（每日／每月上限在 H:\Agent共用\ai-call\deepseek\設定.json）
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


PERM_LOG = os.path.join(HERE, "agent-權限-紀錄.jsonl")   # 只增不改（DeepSeek 03:14）：撤銷也是新增一筆
PERM_PAGE = r"H:\Agent共用\權限總覽.md"                  # 給使用者和大家檢視（使用者 03:14：我批准的也供檢視，以免亂批沒人發現）


def perm_log(rec):
    rec = {"時間": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with open(PERM_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        render_perm_page()
    except Exception as e:
        log(f"權限總覽產生失敗：{type(e).__name__} {e}")


def close_ticket(name, room_id):
    """視窗 agent 在某房間發言後，把它的 待回\\<房間>.md（含它自己做的 *_copy.md）移到 已處理\\。"""
    import window_agent as wa
    home = wa.HOMES.get(name)
    if not home:
        return
    box = os.path.join(home, "群組", "待回")
    done = os.path.join(box, "已處理")
    for fn in (f"{room_id}.md", f"{room_id}_copy.md"):
        p = os.path.join(box, fn)
        if os.path.exists(p):
            try:
                os.makedirs(done, exist_ok=True)
                os.replace(p, os.path.join(done, time.strftime("%m%d-%H%M%S-") + fn))
            except OSError as e:
                log(f"銷單失敗 {name} {fn}：{e}")
    wa.NUDGE_COUNT.pop((name, room_id), None)


def window_allow_lists():
    """讀 agy、OpenCode、Claude Code 的設定檔裡的允許清單（只讀；長字串截斷、像金鑰的遮掉）。"""
    home = os.path.expanduser("~")
    srcs = [("Gemini（agy）", os.path.join(home, ".gemini", "antigravity-cli", "settings.json"),
             lambda d: {"allow": (d.get("permissions") or {}).get("allow", []), "可碰工作區外": d.get("allowNonWorkspaceAccess"),
                        "信任的資料夾": d.get("trustedWorkspaces")}),
            ("輝達（OpenCode）", r"H:\免費模型用\opencode\opencode.json", lambda d: {"permission": d.get("permission")}),
            ("Claude Code（使用者層）", os.path.join(home, ".claude", "settings.json"), lambda d: d.get("permissions") or {}),
            ("Claude Code（使用者層・本機）", os.path.join(home, ".claude", "settings.local.json"), lambda d: d.get("permissions") or {}),
            ("Claude Code（H:\\Claude用 專案）", r"H:\Claude用\.claude\settings.local.json", lambda d: d.get("permissions") or {}),
            ("Claude Code（H:\\Claude用 專案・共用）", r"H:\Claude用\.claude\settings.json", lambda d: d.get("permissions") or {})]
    out = []
    for name, p, pick in srcs:
        if not os.path.exists(p):
            continue
        try:
            d = pick(json.load(open(p, encoding="utf-8-sig")))
        except Exception as e:
            out.append(f"- **{name}**：讀不懂（{type(e).__name__}）")
            continue
        out.append(f"- **{name}**（`{p}`）")
        for k, v in d.items():
            vals = v if isinstance(v, list) else [v] if v not in (None, {}, []) else []
            if isinstance(v, dict):
                vals = [f"{a}: {b}" for a, b in v.items()]
            for x in vals[:40]:
                s = re.sub(r"(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+", r"\1…", str(x))[:160]
                out.append(f"  - {k}：`{s}`")
            if len(vals) > 40:
                out.append(f"  - {k}：……還有 {len(vals) - 40} 條")
    return out or ["- （沒找到設定檔）"]


def render_perm_page():
    """產生 H:\\Agent共用\\權限總覽.md：固定權限（手寫在 權限說明.md）＋目前「以後都允許」＋最近 30 筆批准紀錄。"""
    base = ""
    try:
        base = open(os.path.join(HERE, "權限說明.md"), encoding="utf-8").read().strip()
    except OSError:
        pass
    try:
        g = json.load(open(os.path.join(HERE, "agent-權限.json"), encoding="utf-8"))
    except (OSError, ValueError):
        g = {}
    recs = []
    try:
        recs = [json.loads(l) for l in open(PERM_LOG, encoding="utf-8") if l.strip()]
    except OSError:
        pass
    out = ["# 權限總覽（中樞自動產生，請勿手改）", f"更新：{time.strftime('%Y-%m-%d %H:%M')}｜要改固定權限請在群組提，Claude 改 `權限說明.md`", "",
           base, "", "## 使用者按過「以後都允許」的資料夾（目前有效）"]
    any_g = False
    for name, v in g.items():
        for e in v.get("紀錄", []):
            if e.get("資料夾") in v.get("可寫", []):
                out.append(f"- {name}：`{e['資料夾']}`（{e.get('時間', '')}，{e.get('房間', '')}）")
                any_g = True
    if not any_g:
        out.append("- （沒有）")
    out += ["", "## 最近的批准紀錄（新到舊，最多 30 筆；完整在中樞 agent-權限-紀錄.jsonl）",
            "| 時間 | 請求者 | 動作 | 批准者／來源 | 項目 |", "|---|---|---|---|---|"]
    for r in reversed(recs[-30:]):
        out.append(f"| {r.get('時間', '')} | {r.get('請求者', '')} | {r.get('動作', '')} | {r.get('批准者', '')}／{r.get('來源', '')} | "
                   + "；".join(r.get("項目") or []).replace("|", "／") + " |")
    if not recs:
        out.append("| — | — | — | — | （還沒有） |")
    out += ["", "## 各視窗自己的「永久允許」（中樞只讀抓出來；Gemini 03:18 建議）",
            "你在 agy／OpenCode／Claude Code 視窗裡按的「永遠允許」存在它們自己的設定檔，列在這裡讓大家看得到。"]
    out += window_allow_lists()
    with open(PERM_PAGE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


TG_SETTINGS = os.path.join(HERE, "telegram-設定.json")
TG_NAMES = {"claude": "claude", "codex": "codex", "gemini": "gemini", "輝達": "nvidia", "nvidia": "nvidia",
            "deepseek": "deepseek", "dot": "dot"}
TG_SHOW = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "nvidia": "輝達", "deepseek": "DeepSeek", "dot": "Dot"}


def tg_members(room, text):
    """Telegram 的回話名單（使用者 11-19 08:47；Codex 08:49 補：開頭指令後面可以接正文、每個房間分開存）。
    設定存在 telegram-設定.json：{房間id: {"開": [...], "先回": "claude"}}。
    ・開頭「開 Gemini」「關 Codex」「先回 Gemini」（可連寫多段，用 ；，或換行隔開），後面可以接正文：先存設定、回一行目前設定，再把正文照新設定派送。
    ・開頭點名（@Gemini …）：照點名回答，並把第一個被點名的設成「先回」。
    ・都沒有：照名單問大家，「先回」排第一。回傳 (要繼續處理的文字或 None, 名單)。"""
    try:
        allcfg = json.load(open(TG_SETTINGS, encoding="utf-8"))
        if "開" in allcfg:                      # 舊格式（整份一組）→ 轉成按房間存
            allcfg = {room.id: allcfg}
    except (OSError, ValueError):
        allcfg = {}
    cfg = allcfg.get(room.id) or {"開": ["claude", "codex", "gemini"], "先回": "claude"}
    on, first = list(cfg.get("開", [])), cfg.get("先回", "claude")
    changed = False
    pat = re.compile(r"\s*(開|關|先回)\s*[:：]?\s*((?:[A-Za-z]+|輝達)(?:[ \t,，、]+(?:[A-Za-z]+|輝達))*)\s*(?:[；;。\n]|$)")
    while True:
        m = pat.match(text)
        if not m:
            break
        who = [TG_NAMES[w.lower()] for w in re.findall(r"[A-Za-z]+|輝達", m.group(2)) if w.lower() in TG_NAMES]
        if not who:
            break
        if m.group(1) == "開":
            on += [w for w in who if w not in on]
        elif m.group(1) == "關":
            on = [w for w in on if w not in who]
        else:
            first = who[0]
            on += [first] if first not in on else []
        text, changed = text[m.end():].lstrip(" ，,、；;\n"), True
    mm = re.match(r"\s*@\s*([A-Za-z]+|輝達)", text)
    if mm and mm.group(1).lower() in TG_NAMES:
        first, changed = TG_NAMES[mm.group(1).lower()], True
    if changed:
        allcfg[room.id] = {"開": on, "先回": first, "更新": time.strftime("%m-%d %H:%M")}
        json.dump(allcfg, open(TG_SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        room.notice("Telegram 回話設定（這個房間）：開＝" + ("、".join(TG_SHOW[w] for w in on) or "（沒有）")
                    + f"；先回＝{TG_SHOW.get(first, first)}（指令：「開 名字」「關 名字」「先回 名字」，後面可以接正文；點名誰，誰就變成先回）")
    if not text.strip():
        return None, on
    if mm:
        return text, None                                  # 有點名：照點名走（原本的規則）
    order = [first] + [w for w in ("claude", "codex", "gemini", "nvidia", "deepseek", "dot") if w in on and w != first]
    return text, [w for w in order if w in on or w == first]


def decisions_page(key):
    """待討論頁（使用者 11-19 08:05：「待討論那個連結到一個視窗，弄個留言區，方便一起回覆」）。
    列出待辦裡標題開頭「待討論」、還沒完成的項目，每項有紀錄和留言框；送出＝記進待辦紀錄＋貼到主討論給 Claude。"""
    import html as _h
    try:
        items = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
    except (OSError, ValueError):
        items = []
    rows = []
    for t in items:
        if not str(t.get("標題", "")).startswith("待討論") or t.get("狀態") == "完成":
            continue
        log_ = "".join(f"<li>{_h.escape(str(r.get('時間', '') if isinstance(r, dict) else ''))} {_h.escape(str(r.get('內容', r) if isinstance(r, dict) else r))}</li>"
                       for r in (t.get("紀錄") or [])[-5:])
        rows.append(f"""<section><h3>#{t['id']}　{_h.escape(t['標題'][4:].lstrip('：:'))}</h3>
<div class="meta">負責：{_h.escape(str(t.get('負責', '')))}｜優先：{_h.escape(str(t.get('優先', '')))}｜建立：{_h.escape(str(t.get('建立', '')))}</div>
{f'<ul>{log_}</ul>' if log_ else ''}
<textarea id="t{t['id']}" placeholder="你的回覆（例：同意／不要／改成…）"></textarea>
<button onclick="send({t['id']})">送出</button> <span id="s{t['id']}"></span></section>""")
    body = "\n".join(rows) or "<p>目前沒有待討論的項目。</p>"
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>待討論</title><style>
:root{{--bg:#faf9f6;--card:#fff;--ink:#222;--mute:#777;--line:#e4e1da;--acc:#c96442}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1d1c1a;--card:#262522;--ink:#eee;--mute:#999;--line:#3a3834;--acc:#e08a6a}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:860px;margin:0 auto;padding:16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0}}
h3{{margin:0 0 4px;font-size:16px}} .meta{{color:var(--mute);font-size:13px}} ul{{color:var(--mute);font-size:13px;padding-left:1.2em}}
textarea{{width:100%;box-sizing:border-box;min-height:60px;margin-top:8px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}}
button{{margin-top:6px;background:var(--acc);color:#fff;border:0;border-radius:8px;padding:6px 14px;cursor:pointer}}
</style></head><body><h2>待討論（等你拍板）</h2>
<p class="meta">每項寫完按送出：會記進待辦紀錄，也貼到主討論給 Claude 處理。可以一次回好幾項。</p>
{body}
<script>
const K={json.dumps(key)};
async function send(id){{const t=document.getElementById('t'+id),s=document.getElementById('s'+id);if(!t.value.trim())return;
s.textContent='送出中…';try{{const r=await fetch('/api/decide',{{method:'POST',headers:{{'X-Key':K,'Content-Type':'application/json'}},body:JSON.stringify({{id,text:t.value}})}});
const j=await r.json();s.textContent=j.ok?'✓ 已送出':'送出失敗';if(j.ok)t.value='';}}catch(e){{s.textContent='送出失敗';}}}}
</script></body></html>"""


def grant_write(name, dirs, room=""):
    """使用者按「以後都允許」：把資料夾記進 agent-權限.json（agent 自己改不到這個檔）。"""
    p = os.path.join(HERE, "agent-權限.json")
    try:
        g = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        g = {}
    cur = g.setdefault(name, {}).setdefault("可寫", [])
    log_ = g[name].setdefault("紀錄", [])  # DeepSeek 03:10：誰、何時、哪個房間放的權，查得到；撤銷＝從「可寫」刪掉那行（請 Claude 做）
    for d in dirs:
        if d not in cur:
            cur.append(d)
            log_.append({"資料夾": d, "時間": time.strftime("%Y-%m-%d %H:%M"), "房間": room, "由": "使用者按以後都允許"})
    g[name]["更新"] = time.strftime("%Y-%m-%d %H:%M")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
    log(f"權限：{name} 以後可寫 {dirs}")


def agent_keys():
    """各視窗 agent 的低權限金鑰：secrets/agent-key-<名字>.txt；同一份也放在該 agent 的家（例如 H:\Gemini用\.群組金鑰.txt）。"""
    out = {}
    for name, home in (("Gemini", r"H:\Gemini用"), ("輝達", r"H:\免費模型用")):
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

    def drive_post_loop():
        """App 裡的 AI 從雲端硬碟「群組發言」資料夾貼文（drive_post.py；使用者 11-19）。走自己的 /api/agent_says，跟其他發言同一套去重與點名。"""
        import json as _j, urllib.request as _u
        import drive_post

        def post(who, room, text, cid):
            r = _u.Request(f"http://127.0.0.1:{PORT}/api/agent_says?room={room}", headers={"X-Key": hub.key, "Content-Type": "application/json"},
                           data=_j.dumps({"room": room, "who": who, "text": text, "client_id": cid}).encode())
            return bool(_j.loads(_u.urlopen(r, timeout=15).read()).get("ok"))
        while True:
            try:
                for who, name, res in drive_post.scan(post):
                    log(f"雲端硬碟發言：{who} {name} → {res}")
            except Exception as e:
                log(f"雲端硬碟發言掃描失敗：{type(e).__name__} {e}")
            news = os.path.join(HERE, "terminal", "雲端新檔.txt")  # drive_sync.py 反向鏡像到的新研究檔（11-19 03:4x）
            try:
                if os.path.exists(news) and os.path.getsize(news):
                    with open(news, encoding="utf-8") as f:
                        rows = [l.rstrip("\n").split("\t", 1)[-1] for l in f if l.strip()]
                    os.remove(news)
                    hub.room("g1004308131170").notice(
                        f"📥 雲端硬碟有 {len(rows)} 份新的或更新的研究檔（App 的 GPT／Gemini 放的），已鏡像到 "
                        "`H:\\Agent共用\\專案\\外部研究\\雲端硬碟\\`（索引 `_索引.md`）。一律【未驗證】，用前先核對：\n"
                        + "\n".join(f"• {r}" for r in rows[:15]) + (f"\n……還有 {len(rows) - 15} 份" if len(rows) > 15 else ""))
            except Exception as e:
                log(f"雲端新檔通知失敗：{type(e).__name__} {e}")
            time.sleep(20)
    threading.Thread(target=drive_post_loop, daemon=True).start()
    try:
        render_perm_page()  # 開機先產生一次權限總覽
    except Exception as e:
        log(f"權限總覽產生失敗：{e}")

    def opencode_status_loop():
        """輝達的 OpenCode 在跑時，房間自動顯示「輝達 工作中」（不用它自己回報）；停下來就收掉。"""
        import window_agent as wa
        shown = None
        while True:
            busy = wa.opencode_busy("輝達")
            rid = wa.LAST_ROOM.get("輝達") or "g1004308131170"
            try:
                if busy:
                    if shown and shown != rid:
                        hub.room(shown).set_window_status("輝達", done=True)
                    hub.room(rid).set_window_status("輝達", "OpenCode 執行中")
                    shown = rid
                elif shown:
                    hub.room(shown).set_window_status("輝達", done=True)
                    shown = None
            except Exception as e:
                log(f"輝達狀態更新失敗：{type(e).__name__} {e}")
            time.sleep(3)
    threading.Thread(target=opencode_status_loop, daemon=True).start()

    def status_file_loop():
        """Gemini 11-19 01:58 建議：agy 沙盒連 127.0.0.1 要問使用者，改成寫檔回報。
        <家>\\群組\\狀態.json＝{"room": "...", "text": "正在讀單"}；寫 {"done": true} 或刪檔就收掉。"""
        import json as _j, window_agent as wa
        seen = {}  # 名字 → (mtime, room)
        while True:
            for name, home in wa.HOMES.items():
                p = os.path.join(home, "群組", "狀態.json")
                try:
                    mt = os.path.getmtime(p) if os.path.exists(p) else None
                    old = seen.get(name)
                    if mt is None:
                        if old and old[0] is not None:
                            hub.room(old[1]).set_window_status(name, done=True)
                            seen[name] = (None, old[1])
                        continue
                    if old and old[0] == mt:
                        continue
                    d = _j.load(open(p, encoding="utf-8-sig"))
                    rid = str(d.get("room") or "g1004308131170")
                    if old and old[1] != rid and old[0] is not None:
                        hub.room(old[1]).set_window_status(name, done=True)
                    txt = str(d.get("text") or "").strip()
                    # 11-19 07:11：Gemini 寫 {"text": "結束"} 想收掉，卻被當成一個步驟一直掛著 → 這幾個字也算收掉
                    fin = bool(d.get("done")) or txt in ("結束", "完成", "done", "收掉", "做完")
                    hub.room(rid).set_window_status(name, None if fin else (txt or None), fin)
                    seen[name] = (mt, rid)
                except Exception as e:
                    seen[name] = (os.path.getmtime(p) if os.path.exists(p) else None, (seen.get(name) or (0, "g1004308131170"))[1])
                    log(f"{name} 狀態檔讀不懂：{type(e).__name__} {e}")
            time.sleep(3)
    threading.Thread(target=status_file_loop, daemon=True).start()

    def send_box_loop():
        """Gemini 03:33：視窗 agent 不用跑指令也能發言——寫 <家>\\群組\\發送\\<任意>.json＝{"room": "...", "text": "..."}，
        中樞 3 秒內用它自己的金鑰代貼（同一套去重、點名、自動銷單），貼完刪檔；讀不懂的移到 發送\\退件\\。"""
        import json as _j, hashlib as _h, urllib.request as _u, window_agent as wa
        while True:
            keys = agent_keys()
            for name, home in wa.HOMES.items():
                box = os.path.join(home, "群組", "發送")
                try:
                    os.makedirs(box, exist_ok=True)
                    files = sorted(f for f in os.listdir(box) if f.lower().endswith((".json", ".txt")))
                except OSError:
                    continue
                for fn in files:
                    p = os.path.join(box, fn)
                    try:
                        if time.time() - os.path.getmtime(p) < 2:
                            continue  # 可能還在寫
                        raw = open(p, encoding="utf-8-sig").read()
                        if fn.lower().endswith(".json"):
                            d = _j.loads(raw)
                            rid, text = str(d.get("room") or wa.LAST_ROOM.get(name) or "g1004308131170"), str(d.get("text") or "").strip()  # 沒寫 room：貼回最後叫它的房間（Gemini 03:36）
                        else:  # .txt：第一行可寫「房間: xxx」
                            first, _, rest = raw.partition("\n")
                            rid, text = (first.split(":", 1)[1].strip() if first.startswith("房間") and ":" in first else "g1004308131170",
                                         (rest if first.startswith("房間") else raw).strip())
                        rid = {"互相討論": "g1004308131170", "主討論": "main"}.get(rid, rid)
                        if not text or rid not in hub.rooms:
                            raise ValueError("沒有內容" if not text else f"沒有這個房間 {rid}")
                        cid = "box-" + _h.sha1(f"{name}|{rid}|{text}".encode()).hexdigest()[:20]
                        r = _u.Request(f"http://127.0.0.1:{PORT}/api/agent_says", headers={"X-Agent-Key": keys[name], "Content-Type": "application/json"},
                                       data=_j.dumps({"room": rid, "text": text[:6000], "client_id": cid}).encode())
                        if _j.loads(_u.urlopen(r, timeout=15).read()).get("ok"):
                            os.remove(p)
                            log(f"發送箱：{name} → {rid}（{len(text)} 字）")
                    except Exception as e:
                        try:
                            os.makedirs(os.path.join(box, "退件"), exist_ok=True)
                            os.replace(p, os.path.join(box, "退件", time.strftime("%m%d-%H%M%S-") + fn))
                            with open(os.path.join(box, "退件", fn + ".原因.txt"), "w", encoding="utf-8") as w:
                                w.write(f"{type(e).__name__}: {e}\n")
                        except OSError:
                            pass
                        log(f"發送箱退件：{name} {fn}：{e}")
            time.sleep(3)
    threading.Thread(target=send_box_loop, daemon=True).start()

    def process_alert_loop():
        """過程層自動提醒（使用者 03:25「跑得慢、做得怪也算檢查」；待辦 #18）。只提醒、不介入，同一件事只講一次：
        ① 工作中超過 15 分鐘沒有新步驟（中樞代跑的 agent 和視窗 agent 都算）
        ② 視窗 agent 同一張待回單叫醒到上限（3 次）還沒回
        ③ DeepSeek 單次呼叫花費超過歷史 P95 的 3 倍（搜尋本來就貴，另計）"""
        import window_agent as wa
        sys.path.insert(0, r"H:\Agent共用\ai-call\deepseek")
        STALL = 840  # 比視窗狀態自動消失（900 秒）早一點，才來得及提醒
        seen_steps, warned = {}, set()
        ledger = r"H:\Agent共用\ai-call\deepseek\花費.jsonl"
        try:
            ledger_pos = os.path.getsize(ledger)
        except OSError:
            ledger_pos = 0
        while True:
            now = time.time()
            try:
                for room in list(hub.rooms.values()):
                    cur = [(a.name, tuple(a.steps)) for a in room.agents() if a.busy]
                    cur += [(n, tuple(s["steps"])) for n, s in getattr(room, "window_status", {}).items()]
                    for name, steps in cur:
                        k = (room.id, name)
                        if seen_steps.get(k, (None,))[0] != steps:
                            seen_steps[k] = (steps, now)
                        elif now - seen_steps[k][1] > STALL and (k, steps) not in warned:
                            warned.add((k, steps))
                            room.notice(f"⏱ 提醒：{name} 工作中已 {int((now - seen_steps[k][1]) // 60)} 分鐘沒有新步驟"
                                        f"（停在「{(steps[-1] if steps else '開始')[:40]}」）。可能卡住或在跑長工作，要不要看一下由你決定。")
                    active = {n for n, _ in cur}
                    for k in [k for k in seen_steps if k[0] == room.id and k[1] not in active]:
                        seen_steps.pop(k, None)
                for (name, rid), n in list(wa.NUDGE_COUNT.items()):
                    if n >= wa.NUDGE_MAX and ("nudge", name, rid) not in warned:
                        warned.add(("nudge", name, rid))
                        r = hub.rooms.get(rid) or hub.room("g1004308131170")
                        r.notice(f"⏱ 提醒：{name} 這張待回單已經叫醒 {n} 次還沒回，不再自動叫。可能卡在視窗的確認框，或視窗沒開。")
                    elif n < wa.NUDGE_MAX:
                        warned.discard(("nudge", name, rid))
                size = os.path.getsize(ledger) if os.path.exists(ledger) else 0
                if size > ledger_pos:
                    with open(ledger, encoding="utf-8") as f:
                        f.seek(ledger_pos)
                        new = [json.loads(l) for l in f.read().splitlines() if l.strip()]
                    ledger_pos = size
                    hist = sorted(json.loads(l).get("cost", 0) for l in open(ledger, encoding="utf-8")
                                  if l.strip() and "-搜尋" not in l)
                    if len(hist) >= 20:
                        p95 = hist[int(len(hist) * 0.95) - 1]
                        for r_ in new:
                            if "-搜尋" not in str(r_.get("who")) and r_.get("cost", 0) > max(3 * p95, 0.005):
                                hub.room("g1004308131170").notice(
                                    f"⏱ 提醒：DeepSeek 一次呼叫花了 ${r_['cost']:.4f}，是平常（P95 ${p95:.4f}）的 {r_['cost'] / p95:.0f} 倍"
                                    f"（輸入 {r_.get('in')} token）。可能讀了大檔或對話太長。")
            except Exception as e:
                log(f"過程提醒失敗：{type(e).__name__} {e}")
            time.sleep(60)
    threading.Thread(target=process_alert_loop, daemon=True).start()

    def shared_guard_loop():
        """共用區保護（使用者 11-19 05:3x：「偵測到異常就改回鏡像」；06:3x 開了雙向雲端資料夾）。
        ① 每 10 分鐘：保護檔有沒進 git 的改動超過 3 分鐘＝不是 Claude 改的（Claude 改完立刻 commit）→ 另存被改版、改回 git 版、群組提醒。
        ② 每小時：其他檔自動 commit 一次（快照），讓「最後一版」不會太舊。"""
        import subprocess as sp
        root = r"H:\Agent共用"
        protected = ["README.md", "新成員入門.md", "團隊目標.md", "通知規則.txt", ".gitignore",
                     "記憶/規則.json", "記憶/規則.md", "記憶/注入.py", "待辦/待辦.py", "課程/課程.py",  # 這些資料夾使用者 06:3x 設成雲端雙向，程式檔會被中樞或大家執行，一定要保護
                     ".agents/skills/team-system/SKILL.md", ".claude/skills/team-system/SKILL.md"]
        backup = os.path.join(HERE, "共用區被改備份")
        first_seen, last_commit = {}, 0.0

        def git(*a):
            return sp.run(["git", "-C", root, "-c", "core.quotepath=false", "-c", "user.name=中樞自動", "-c", "user.email=hub@local", *a],
                          capture_output=True, timeout=120, creationflags=0x08000000 if os.name == "nt" else 0)
        while True:
            try:
                changed = set(git("diff", "--name-only", "--", *protected).stdout.decode("utf-8", "replace").split("\n")) - {""}
                now = time.time()
                for f in list(first_seen):
                    if f not in changed:
                        first_seen.pop(f)
                for f in changed:
                    first_seen.setdefault(f, now)
                    if now - first_seen[f] < 180:
                        continue
                    src = os.path.join(root, f.replace("/", os.sep))
                    os.makedirs(backup, exist_ok=True)
                    keep = os.path.join(backup, time.strftime("%m%d-%H%M%S-") + f.replace("/", "_"))
                    if os.path.exists(src):
                        shutil.copy2(src, keep)
                    git("checkout", "--", f)
                    first_seen.pop(f, None)
                    log(f"共用區保護：{f} 被改，已改回 git 版，被改版存 {keep}")
                    hub.room("g1004308131170").notice(
                        f"🛡 共用區保護：`{f}` 有沒經過 Claude 的改動（可能是雲端 App 或哪位 AI），已改回上一版。"
                        f"被改的內容沒丟，存在中樞 `共用區被改備份\\{os.path.basename(keep)}`；要保留請在群組說，Claude 會併回去。")
                # 單一來源的副本（使用者 06:58）：team-system 技能入口只改中樞的 skill-team-system.md，六份副本由這裡覆蓋
                src_skill = os.path.join(HERE, "skill-team-system.md")
                if os.path.exists(src_skill):
                    body = open(src_skill, encoding="utf-8").read()
                    for home in (root, r"H:\Gemini用", r"H:\免費模型用"):
                        for sub in (".agents", ".claude"):
                            dst = os.path.join(home, sub, "skills", "team-system", "SKILL.md")
                            try:
                                if not os.path.exists(dst) or open(dst, encoding="utf-8").read() != body:
                                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                                    open(dst, "w", encoding="utf-8").write(body)
                                    log(f"技能入口副本已由來源覆蓋：{dst}")
                            except OSError:
                                pass
                if now - last_commit > 3600:
                    git("add", "-A")
                    r = git("commit", "-q", "-m", "中樞每小時自動快照")
                    last_commit = now
                    if r.returncode == 0:
                        log("共用區自動快照已 commit")
            except Exception as e:
                log(f"共用區保護失敗：{type(e).__name__} {e}")
            time.sleep(600)
    threading.Thread(target=shared_guard_loop, daemon=True).start()

    def deps_done(t, items):
        """待辦的「依賴」欄（例：[6, 21] 或 "6,21"）：前置項目都完成才派（使用者 07:20：手搓＋訓練都吃題目設計，目標彼此連動）。"""
        d = t.get("依賴") or []
        ids = [int(x) for x in re.findall(r"\d+", str(d))] if not isinstance(d, list) else [int(x) for x in d]
        st = {x.get("id"): x.get("狀態") for x in items}
        return all(st.get(i) == "完成" for i in ids)

    def cpu_ok(t):
        """待辦的「資源」欄寫 CPU（訓練、窮舉這類吃 CPU 的）：有人在用就先不派（使用者 07:20：訓練卡 CPU 速度）。
        佔用的人在 H:\\Agent共用\\待辦\\CPU使用中.txt 寫一行「誰｜做什麼｜預計到幾點」，做完刪掉；超過 6 小時的當作忘了刪。"""
        if "CPU" not in str(t.get("資源", "")).upper():
            return True
        return cpu_slots_used() < CPU_SLOTS

    CPU_SLOTS = 3  # Codex 07:22：最多三個訓練 worker（BLAS 單執行緒），用「執行槽」而不是一件就鎖死全部

    def cpu_slots_used():
        """`待辦\\CPU使用中.txt` 每行一件：「PID｜誰｜做什麼｜預計到幾點」。PID 還活著才算佔一槽（Gemini／Codex 07:22：查程序，不只看時間）。
        沒寫 PID 的行，6 小時內算佔用。"""
        p = r"H:\Agent共用\待辦\CPU使用中.txt"
        if not os.path.exists(p):
            return 0
        n = 0
        fresh = time.time() - os.path.getmtime(p) < 6 * 3600
        for line in open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"(\d+)\s*[｜|]", line)
            if m:
                r = subprocess.run(["tasklist", "/FI", f"PID eq {m.group(1)}", "/NH"], capture_output=True,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
                n += m.group(1) in r.stdout.decode("utf-8", "replace")
            else:
                n += fresh
        return n

    def dispatch_loop():
        """派工員 v1（使用者 11-19 05:08：「沒事的話系統照演算法派工，幫忙調控用量」；06:45：輝達今天還好多次數）。
        每 30 分鐘：輝達視窗開著、沒在跑、沒有待回單 → 從待辦挑一件它能做的（負責＝輝達 或 誰能做 含輝達；高＞中＞低；
        跳過完成／等待／待討論），用叫醒通道送「請做一小步」。只派輝達（免費）；其他人之後再加。"""
        import window_agent as wa
        name, box = "輝達", r"H:\免費模型用\群組\待回"
        rank = {"高": 0, "中": 1, "低": 2}
        last, warned_day = {}, {}
        while True:
            time.sleep(1800)
            try:
                _t = wa.nvidia_total_today()                 # 合計（worker＋OpenCode），跟用量條同一個函式
                calls = _t[0] if _t else None
                if calls is not None and calls >= 0.9 * wa.NVIDIA_DAILY_BUDGET and not warned_day.get(time.strftime("%m%d")):
                    warned_day[time.strftime("%m%d")] = True
                    hub.room("g1004308131170").notice(f"⏱ 輝達今天已呼叫模型 {calls} 次（預算 {wa.NVIDIA_DAILY_BUDGET}），快到上限，派工暫停。")
                cap, _rel, _left = wa.nvidia_dispatch_limit()  # 平時 70%；最後 4 小時 900＋分段還回的保留額（使用者 07:15）
                if calls is not None and calls >= cap:
                    continue
                if wa.opencode_busy(name) is not False:      # None＝視窗沒開；True＝在忙
                    continue
                if any(f.endswith(".md") for f in os.listdir(box)):
                    continue                                  # 還有人找它，先回人
                items = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
                cand = [t for t in items if t.get("狀態") not in ("完成", "等", "等待")
                        and not str(t.get("標題", "")).startswith("待討論")
                        and (t.get("負責") == name or name in str(t.get("誰能做", "")))
                        and deps_done(t, items) and cpu_ok(t)]
                if not cand:
                    continue
                cand.sort(key=lambda t: (rank.get(t.get("優先"), 3), last.get(t["id"], 0)))
                t = cand[0]
                last[t["id"]] = time.time()
                text = (f"【中樞派工】你現在沒事，請做待辦 #{t['id']} 的一小步（30 分鐘內做得完的量）：{t.get('標題', '')}。"
                        f"怎麼驗：{t.get('怎麼驗', '照接手卡')}。開始前 `PY H:\\Agent共用\\待辦\\待辦.py 做 {t['id']} \"輝達：做哪一步\"`；"
                        "做完把結果（指令＋輸出或檔名）用發送箱貼到互相討論群（room g1004308131170）。卡住就寫「@Claude 請幫我：」。")
                r = wa.nudge(name, "g1004308131170", custom=text)
                with open(os.path.join(HERE, "logs", "派工.log"), "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%m-%d %H:%M')} {name} #{t['id']} {r}\n")
                if r == "已送進視窗":
                    hub.room("g1004308131170").notice(f"📋 派工：輝達閒著，派待辦 #{t['id']}（{str(t.get('標題', ''))[:40]}）的一小步。")
            except Exception as e:
                log(f"派工失敗：{type(e).__name__} {e}")
    threading.Thread(target=dispatch_loop, daemon=True).start()

    def deepseek_audit_loop():
        """每小時對一次 DeepSeek 金鑰帳（使用者 11-19 01:56：定期查詢金鑰消費）；有警告才在互相討論群講。"""
        sys.path.insert(0, r"H:\Agent共用\ai-call\deepseek")
        import ds
        last = None
        while True:
            try:
                w = ds.audit().get("警告") or []
                if w and w != last:
                    hub.room("g1004308131170").notice("DeepSeek 對帳：" + "；".join(w))
                last = w
            except Exception as e:
                log(f"DeepSeek 對帳失敗：{type(e).__name__} {e}")
            try:  # 11-19 04:2x：Gemini 搬進視窗後用量條沒人更新，看錯以為還有 → Argon 用到 99%。/usage 不呼叫模型、不花額度
                subprocess.run([sys.executable.replace('pythonw.exe', 'python.exe'), '-X', 'utf8', r'H:\Agent共用\ai-call\gemini_usage.py'],
                               capture_output=True, timeout=180, creationflags=0x08000000 if os.name == 'nt' else 0)
            except Exception as e:
                log(f'Gemini 用量更新失敗：{type(e).__name__} {e}')
            try:
                render_perm_page()  # 每小時也刷新權限總覽（視窗裡按的允許會反映出來）
            except Exception as e:
                log(f"權限總覽產生失敗：{e}")
            time.sleep(3600)
    threading.Thread(target=deepseek_audit_loop, daemon=True).start()

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
