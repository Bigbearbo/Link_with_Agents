"""Claude Code 與 Codex 的呼叫封裝（三方討論用）。

- 各自保有自己的 session，只把對方還沒看過的新發言轉給它。
- 串流讀取輸出，隨時更新 self.steps（正在做什麼），讓網頁顯示進度。
- write=False 唯讀；write=True 可在 WORKDIR 內寫檔與執行指令。
"""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time

WORKDIR = os.path.abspath(os.environ.get("LINK_WITH_WORKDIR", r"H:\Codex用"))  # 共用工作區；別台電腦用環境變數指定
PROMPT_LIMIT = 60  # 一次最多帶給 agent 的新訊息數          # 實驗目前的主工作區（D:\Codex用 為唯讀備份）
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PREAMBLE = (
    "【三方討論規則】你正在一個三方討論視窗裡，參與者：使用者、Claude（Anthropic 的 Claude Code）、"
    "Codex（OpenAI 的 Codex）。使用者可能用電腦網頁或手機 Telegram 發言，兩邊是同一串討論。\n"
    f"- 實驗主工作區是 {WORKDIR}（倉鼠研究與小網路訓練）；D:\\Codex用 是唯讀備份，不要寫入。\n"
    "{perm}"
    "- 用繁體中文回答，簡潔，盡量 15 行內，有根據時附檔案路徑。\n"
    "- 你會收到其他參與者的新發言，格式是【發言者｜時間】內容。可以同意、反駁或補充對方。\n"
    "- 想查以前講過什麼：python -B H:\\Claude用\\三方討論\\search.py 關鍵字 [--who 使用者] [--room 主討論] [--since MM-DD]（搜尋全部對話紀錄）。\n"
    "- 訊息開頭若標「（只問 X）」，代表使用者只問 X，另一位看到即可，不必回應那則。\n"
    "- 只輸出你自己的發言，不要替別人發言。\n\n"
)
PERM_RO = "- 目前是唯讀模式：只讀檔，不修改任何檔案、不啟動訓練、不寫 queue、不呼叫外部隊員。\n"
PERM_RW = (f"- 目前是可寫模式：你可以在 {WORKDIR} 內讀寫檔案、執行指令。改檔前先簡短說明要改什麼，"
           "完成後回報改了哪些檔案。不要啟動或停止訓練、不改已封存的實驗來源、不寫 queue、不呼叫外部隊員，"
           f"除非使用者明確要求。遵守 {WORKDIR}\\AGENTS.md。兩位都能寫檔，動手前先講好分工，避免同時改同一個檔案。"
           "大工作請先說預計做什麼，做完再詳細回報。\n"
           "- Claude 的權限：一般操作直接做；有安全疑慮的動作會被系統擋下，並自動請使用者在討論中按「允許一次」。"
           "被擋下時，請用一兩句話說明你想做什麼、為什麼需要，然後停下來等使用者決定，不要繞路規避。\n")
PERM_NOTE = {False: "【權限變更】已切回唯讀模式。" + PERM_RO, True: "【權限變更】已切換為可寫模式。" + PERM_RW}
WORKDIR_NOTE = (f"【工作區變更】實驗已搬到 {WORKDIR}，之後讀寫都用這裡；D:\\Codex用 只是唯讀備份，不要再寫入。"
                "若剛才有寫進 D:\\Codex用 的東西，請說明是哪些檔案。")


def find_codex():
    hits = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin", "*", "codex.exe"))
    hits.sort(key=os.path.getmtime, reverse=True)
    return hits[0] if hits else shutil.which("codex")


def find_claude():
    hit = shutil.which("claude")
    if hit:
        return hit
    for p in (os.path.expanduser(r"~\.local\bin\claude.exe"), os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if os.path.exists(p):
            return p
    return None


USAGE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.jsonl")


def log_usage(**rec):
    """每次呼叫記一筆用量（花費或 token），給 status.py 統計。"""
    rec["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


CLAUDE_EXE = find_claude()
CODEX_EXE = find_codex()


def self_check():
    """啟動自我檢查：Claude 能不能回話、Codex 能不能真的執行指令。回傳 {名稱: (是否正常, 說明)}。"""
    out = {}
    if not CLAUDE_EXE:
        out["Claude"] = (False, "找不到 claude 程式")
    else:
        try:
            r = subprocess.run([CLAUDE_EXE, "-p", "--output-format", "json", "--permission-mode", "dontAsk"],
                               input="只回答 OK".encode(), capture_output=True, timeout=120, cwd=WORKDIR,
                               creationflags=NO_WINDOW)
            data = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
            out["Claude"] = (not data.get("is_error"), "正常" if not data.get("is_error") else str(data.get("result"))[:200])
        except Exception as e:
            out["Claude"] = (False, str(e)[:200])
    if not CODEX_EXE:
        out["Codex"] = (False, "找不到 codex 程式")
    else:
        try:
            r = subprocess.run([CODEX_EXE, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                                "-C", WORKDIR, "-"],
                               input="用 shell 執行 `echo rt-selfcheck` 一次，然後只回答指令輸出。".encode(),
                               capture_output=True, timeout=180, cwd=WORKDIR, creationflags=NO_WINDOW)
            text = r.stdout.decode("utf-8", "replace")
            if "requires an installed app" in text or "exec_command failed" in text:
                out["Codex"] = (False, "Codex 能回話，但不能執行指令（本機執行工具啟動失敗）")
            elif "rt-selfcheck" in text:
                out["Codex"] = (True, "正常")
            else:
                out["Codex"] = (False, "沒有得到預期的指令輸出")
        except Exception as e:
            out["Codex"] = (False, str(e)[:200])
    return out


def short(s, n=90):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


class Agent:
    def __init__(self, name):
        self.name = name
        self.session = None
        self.seen = 0
        self.proc = None
        self.write = False
        self.steps = []
        self.started = None
        self.busy = False      # 由 hub 管理
        self.fork_from = None  # 分身：從這個對話分出來（不會跟主線搶同一個對話）
        self.side = None       # 目前正在跑的分身
        self.on_say = None     # 中途每說一段話就呼叫（讓房間立刻貼出來）
        self.said = []         # 這一輪已經貼出去的話
        self.denials = []      # 上一輪被權限擋下的動作
        self.extra_allow = []  # 使用者允許一次的規則
        self.pending = False   # 忙碌中又有新訊息：做完再回一輪

    def say(self, text):
        text = (text or "").strip()
        if not text:
            return
        self.said.append(text)
        if self.on_say:
            try:
                self.on_say(text)
            except Exception:
                pass

    def step(self, text):
        self.steps.append(short(text))
        del self.steps[:-8]

    def build_prompt(self, transcript):
        new = transcript[self.seen:]
        skipped = max(0, len(new) - PROMPT_LIMIT)  # 新對話或太久沒輪到：只帶最近 PROMPT_LIMIT 則
        new = new[skipped:]
        body = ("" if not skipped else f"（較早的 {skipped} 則省略；需要的話用 H:\\Claude用\\三方討論\\search.py 搜尋）\n\n") + "\n\n".join(
            f"【{m['who']}（分身已先回答）｜{m.get('time', '')}】{m['text']}" if m["who"] == self.name else f"【{m['who']}｜{m.get('time', '')}】{m['text']}"
            for m in new if m["who"] != self.name or m.get("kind") == "side")
        if not body:
            body = "（沒有新發言，請繼續補充或回應目前的討論。）"
        head = PREAMBLE.replace("{perm}", PERM_RW if self.write else PERM_RO) if self.session is None else ""
        return head + body + f"\n\n現在輪到你（{self.name}）發言。"

    def _spawn(self, cmd, prompt):
        if not cmd[0]:
            raise RuntimeError(f"找不到 {self.name} 的程式，請確認已安裝")
        self.steps, self.started, self.said = [], time.time(), []
        self.proc = subprocess.Popen(cmd, cwd=WORKDIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        err_chunks = []
        threading.Thread(target=lambda p=self.proc: err_chunks.append(p.stderr.read()), daemon=True).start()
        self.proc.stdin.write(prompt.encode("utf-8"))
        self.proc.stdin.close()
        return err_chunks

    def _lines(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

    def _finish(self, err_chunks):
        rc = self.proc.wait()
        self.proc = None
        time.sleep(0.05)
        return rc, b"".join(c for c in err_chunks if c).decode("utf-8", "replace")


class ClaudeAgent(Agent):
    TOOL_LABEL = {"Read": "讀檔", "Glob": "找檔案", "Grep": "搜尋", "Edit": "修改", "Write": "寫檔",
                  "Bash": "執行", "PowerShell": "執行", "WebFetch": "讀網頁", "WebSearch": "搜尋網路"}

    def run(self, prompt):
        self.denials = []
        cmd = [CLAUDE_EXE, "-p", "--output-format", "stream-json", "--verbose", "--add-dir", WORKDIR]
        if self.write:
            # 一般操作自動放行，有安全疑慮的由 auto 分類器擋下（記在 permission_denials）
            cmd += ["--permission-mode", "auto"]
            if self.extra_allow:
                cmd += ["--allowedTools", ",".join(self.extra_allow)]
        else:
            cmd += ["--allowedTools", "Read,Glob,Grep", "--permission-mode", "dontAsk"]
        if self.fork_from:
            cmd += ["--resume", self.fork_from, "--fork-session"]
        elif self.session:
            cmd += ["--resume", self.session]
        err = self._spawn(cmd, prompt)
        result = None
        for ev in self._lines():
            if ev.get("session_id") and not self.fork_from:
                self.session = ev["session_id"]
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        what = (inp.get("file_path") or inp.get("command") or inp.get("pattern")
                                or inp.get("path") or inp.get("url") or "")
                        self.step(f"{self.TOOL_LABEL.get(c.get('name'), c.get('name'))}：{what}")
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        self.say(c["text"])
                        self.step("說了一段話")
            elif ev.get("type") == "result":
                result = ev
        rc, errtext = self._finish(err)
        if result is None:
            raise RuntimeError(f"Claude 沒有回覆（exit {rc}）：{errtext[-500:]}")
        self.denials = [d for d in (result.get("permission_denials") or []) if isinstance(d, dict)]
        u = result.get("usage") or {}
        log_usage(agent="Claude", kind="fork" if self.fork_from else "turn", cost_usd=result.get("total_cost_usd"),
                  input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                  cache_read=u.get("cache_read_input_tokens"), seconds=round(time.time() - (self.started or time.time()), 1))
        if result.get("is_error"):
            raise RuntimeError(f"Claude 錯誤：{result.get('result')}")
        return (result.get("result") or "").strip()


class CodexAgent(Agent):
    def run(self, prompt):
        mode = "workspace-write" if self.write else "read-only"
        if self.fork_from:
            cmd = [CODEX_EXE, "exec", "fork", self.fork_from, "--json", "--ephemeral", "--skip-git-repo-check",
                   "-c", f'sandbox_mode="{mode}"', "-"]
        elif self.session:
            cmd = [CODEX_EXE, "exec", "resume", self.session, "--json", "--skip-git-repo-check",
                   "-c", f'sandbox_mode="{mode}"', "-"]
        else:
            cmd = [CODEX_EXE, "exec", "--json", "--skip-git-repo-check", "-s", mode, "-C", WORKDIR, "-"]
        err = self._spawn(cmd, prompt)
        msgs, errors = [], []
        for ev in self._lines():
            t, item = ev.get("type"), ev.get("item") or {}
            it = item.get("type")
            if t == "thread.started" and not self.fork_from:
                self.session = ev.get("thread_id") or self.session
            elif t == "item.started" and it == "command_execution":
                c = str(item.get("command", ""))
                c = re.sub(r'^.*?(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?)"?\s+(?:-NoProfile\s+)?(?:-Command|/c)\s+', "", c, flags=re.I)
                self.step(f"執行：{c.strip(chr(34) + chr(39))}")
            elif t == "item.completed":
                if it == "agent_message":
                    msgs.append(item.get("text", ""))
                    self.say(item.get("text", ""))
                    self.step("說了一段話")
                elif it == "file_change":
                    paths = ", ".join(os.path.basename(c.get("path", "")) for c in item.get("changes") or [])
                    self.step(f"修改：{paths}")
                elif it == "reasoning" and item.get("text"):
                    self.step(f"思考：{item['text']}")
                elif it in ("web_search", "mcp_tool_call"):
                    self.step(f"{'搜尋網路' if it == 'web_search' else '工具'}：{item.get('query') or item.get('tool', '')}")
            elif t == "turn.completed":
                u = ev.get("usage") or {}
                log_usage(agent="Codex", kind="fork" if self.fork_from else "turn",
                          input_tokens=u.get("input_tokens"), cached_input=u.get("cached_input_tokens"),
                          output_tokens=u.get("output_tokens"), reasoning=u.get("reasoning_output_tokens"),
                          seconds=round(time.time() - (self.started or time.time()), 1))
            elif t in ("error", "turn.failed"):
                errors.append(json.dumps(ev, ensure_ascii=False))
        rc, errtext = self._finish(err)
        if not msgs:
            raise RuntimeError(f"Codex 沒有回覆（exit {rc}）：{' '.join(errors)[-500:]}\n{errtext[-500:]}")
        return "\n\n".join(m.strip() for m in msgs if m.strip())


def describe_denial(d):
    """把一筆 permission_denial 轉成（給人看的說明, 允許一次用的規則）。"""
    tool = d.get("tool_name") or "?"
    inp = d.get("tool_input") or {}
    if tool in ("Bash", "PowerShell") and inp.get("command"):
        return f"{tool}：{inp['command']}", f"{tool}({inp['command']})"
    target = inp.get("file_path") or inp.get("url") or inp.get("path") or inp.get("pattern") or ""
    if target and tool in ("Edit", "Write", "Read", "NotebookEdit", "WebFetch"):
        return f"{tool}：{target}", (f"{tool}(domain:{target.split('/')[2]})" if tool == "WebFetch" and "//" in target
                                     else f"{tool}({target})")
    return f"{tool}：{json.dumps(inp, ensure_ascii=False)[:200]}", tool


# ---------- Gemini（Google，經 agy CLI）：第三位參與者，永遠唯讀 ----------
AGY_EXE = r"H:\Agent共用\ai-call\tools\antigravity-cli\bin\agy.exe"
GEMINI_MODEL = "gemini-4-hydrogen-high"
GEMINI_WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini-work")  # 提示檔與權限設定（Gemini 只能讀）
GEMINI_HOME = r"H:\Gemini用"  # Gemini 自己的資料夾：唯一可以寫的地方
GEMINI_ROLE = (
    "【你的身分】你是 Gemini（Google），討論裡的第三位 AI 參與者（另兩位是 Claude、Codex）。"
    "權限：你自己的資料夾 H:\Gemini用 可以讀寫（筆記、草稿、分析結果放這裡）；"
    "實驗 H:\Codex用、共用區 H:\Agent共用、對話紀錄 H:\Claude用\三方討論\logs 只能讀；不能執行指令、不能啟動訓練。"
    "要放進共用區的東西，先寫在 H:\Gemini用，再請 Claude 搬（共用區目前由 Claude 代管你的寫入）。"
    "下面規則若寫「唯讀模式」，對你的意思是：除了 H:\Gemini用 以外都唯讀。"
    "你的額度在 H:\Agent共用\gemini-用量.json（每輪後自動更新；看「判斷」和 speed，超速就少讀大檔、縮短回覆）。"
    "模型由你自己決定：把下一輪要用的模型 ID 寫在 H:\Gemini用\模型.txt 第一行（可選 gemini-4-hydrogen-low/medium/high/xhigh、"
    "gemini-4-argon-low/medium/high、gemini-3.8-flash-low/medium/high；不寫或寫錯就用 gemini-4-hydrogen-high）。"
    "簡單的確認、短回覆用 flash，深度審查用 hydrogen-high/xhigh，Argon 有自己獨立的週額度；依 gemini-用量.json 自己調。注意：Hydrogen 和 Flash 共用同一個額度池，耗量依 token 成本計，高階模型燒得快；5 小時用超過 70% 或週額度超速時，程式會強制把你降到 flash。"
    "你的長處是審查、找反例與漏洞、檢查數學與數字、提出別人沒想到的解釋。"
    "別人已經說過的不要重複；同意就簡短說同意，重點放在補充或指出問題。"
    "訊息開頭標「（只問 Gemini）」是使用者只問你；標「（只問 Claude／Codex）」的不用回。\n\n")


class GeminiAgent(Agent):
    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass  # 不管房間開不開可寫，Gemini 都只讀

    def build_prompt(self, transcript):
        p = super().build_prompt(transcript)
        return (GEMINI_ROLE + p) if self.session is None else p

    def run(self, prompt):
        os.makedirs(GEMINI_WORK, exist_ok=True)
        if len(prompt) > 6000:  # 命令列有長度上限：長內容放檔案請它先讀
            pf = os.path.join(GEMINI_WORK, time.strftime("prompt-%m%d-%H%M%S.txt"))
            open(pf, "w", encoding="utf-8").write(prompt)
            for old in sorted(glob.glob(os.path.join(GEMINI_WORK, "prompt-*.txt")))[:-20]:
                try:
                    os.remove(old)
                except OSError:
                    pass
            arg = f"請先完整讀 {pf}：那是這一輪的討論內容與規則。照裡面的指示，只輸出你這一輪的發言。"
        else:
            arg = prompt
        # 權限：工作區＝H:\Gemini用（只有這裡能寫）；其他地方靠 agy-settings.json 的 read_file 白名單只能讀；
        # 指令一律不允許（headless 沒有白名單就自動拒絕）。實測 2026-11-17：寫共用區被擋、寫自己資料夾成功。
        model = gemini_model()
        self.step(f"模型：{model}")
        cmd = [AGY_EXE, "-p", arg, "--model", model, "--sandbox",
               "--settings", os.path.join(GEMINI_WORK, "agy-settings.json"),
               "--output-format", "json", "--print-timeout", "900s"]
        if self.session:
            cmd += ["--conversation", self.session]
        if not os.path.exists(AGY_EXE):
            raise RuntimeError("找不到 Gemini 的 agy 程式")
        self.steps, self.started, self.said = [], time.time(), []
        self.step("Gemini 思考中")
        os.makedirs(GEMINI_HOME, exist_ok=True)
        self.proc = subprocess.Popen(cmd, cwd=GEMINI_HOME, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        out, err = self.proc.communicate()
        rc, self.proc = self.proc.returncode, None
        try:
            data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception:
            raise RuntimeError(f"Gemini 沒有正常回覆（code {rc}）：{err.decode('utf-8', 'replace')[-300:]}")
        if data.get("conversation_id"):
            self.session = data["conversation_id"]
        text = (data.get("response") or "").strip()
        if not text and data.get("denied_actions"):
            acts = "、".join(d.get("display_name", "?") for d in data["denied_actions"])
            text = (f"（我想用 {acts} 但被擋下：我只能寫 H:\Gemini用，其他地方只能讀、也不能執行指令。"
                    "需要放進共用區或執行的，請 Claude 代做。）")
        if data.get("status") != "SUCCESS" and not text:
            raise RuntimeError(f"Gemini 回覆失敗：{data.get('status')}")
        threading.Thread(target=refresh_gemini_usage, daemon=True).start()  # 每輪後更新額度檔（不花額度）
        self.say(text)
        return text


GEMINI_ALLOWED = re.compile(r"gemini-(4-(hydrogen|argon)-(low|medium|high|xhigh)|3\.8-flash-(low|medium|high))")


def gemini_model():
    """Gemini 自己決定下一輪用哪個模型：讀 H:\Gemini用\模型.txt 第一行（只接受 Gemini 系列，不認得就用預設）。"""
    try:
        first = open(os.path.join(GEMINI_HOME, "模型.txt"), encoding="utf-8-sig").readline().strip()
        chosen = first if GEMINI_ALLOWED.fullmatch(first) else GEMINI_MODEL
    except OSError:
        chosen = GEMINI_MODEL
    # 額度保護（使用者 11-17：谷歌額度一乾就只剩最弱模型）：Hydrogen 和 Flash 共用同一池，
    # 5 小時用超過 70% 或週額度超速 → 強制降到 flash；週用超過 85% → flash-low。Argon 另計不受限。
    try:
        u = json.load(open(r"H:\Agent共用\gemini-用量.json", encoding="utf-8"))
        h5, wk = u.get("five_hour") or {}, u.get("seven_day") or {}
        if "argon" not in chosen:
            if wk.get("used", 0) >= 85:
                return "gemini-3.8-flash-low"
            if h5.get("used", 0) >= 70 or wk.get("state") == "超速":
                return chosen if "flash" in chosen else "gemini-3.8-flash-high"
    except Exception:
        pass
    return chosen


def refresh_gemini_usage():
    try:
        subprocess.run(["python", "-B", r"H:\Agent共用\ai-call\gemini_usage.py"], capture_output=True,
                       timeout=150, creationflags=NO_WINDOW)
    except Exception:
        pass


# ---------- 輝達（OpenRouter 免費模型，經 Codex 的 worker）：第四位參與者，只有文字 ----------
NVIDIA_HOME = r"H:\免費模型用"
NVIDIA_CONTEXT = 60          # 每次都帶最近幾則（它沒有對話記憶）
NVIDIA_CHAR_LIMIT = 60000    # 太長就從舊的開始砍
NVIDIA_ROLE = (
    "你是「輝達」（NVIDIA Nemotron，經 OpenRouter 免費額度），在一個多方討論房裡當第四位 AI 參與者"
    "（其他是使用者、Claude、Codex、Gemini）。研究主題是倉鼠世界與螺旋語、小型神經網路的訓練與手填權重。\n"
    "- 你只能看到下面附的對話文字，不能讀檔、不能執行程式、不能上網；不知道的就說不知道，不要編造檔案內容或數字。\n"
    "- 用繁體中文，簡潔，盡量 12 行內；只輸出你自己的發言，不要替別人說話。\n"
    "- 訊息開頭標「（只問 輝達）」是使用者點名你；你被叫到時就回答最後那個點名你的問題，其他內容當背景。\n\n")


def _paths():
    try:
        return json.load(open(r"H:\Agent共用\ai-call\paths.json", encoding="utf-8"))
    except Exception:
        return {"worker": r"H:\Agent共用\ai-call\tools\openrouter-worker\worker.py"}


class NvidiaAgent(Agent):
    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass

    def build_prompt(self, transcript):
        c = nvidia_cfg()
        msgs = [m for m in transcript if not (c["略過系統通知"] and m["who"] == "系統")][-c["最近幾則"]:]
        parts = [f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in msgs]
        while parts and sum(len(p) for p in parts) > c["字數上限"]:
            parts.pop(0)
        rule = (f"【你的載入設定】最近幾則={c['最近幾則']} 字數上限={c['字數上限']} "
                f"略過系統通知={'是' if c['略過系統通知'] else '否'}（這次實際帶了 {len(parts)} 則）。"
                "這是你自己決定的：如果覺得內容太多讓你抓不到重點、或太少缺背景，可以在回覆最後一行寫"
                "「【載入設定】最近幾則=30 字數上限=20000 略過系統通知=是」（只寫要改的項目），下一輪生效；"
                "範圍：最近幾則 5–150、字數上限 2000–120000。要跟 Claude 協調（例如載入規則、要它幫你讀檔摘要）就在發言裡寫 @Claude，它會回你。\n\n")
        return NVIDIA_ROLE + rule + "以下是最近的對話：\n\n" + "\n\n".join(parts) + "\n\n現在輪到你（輝達）發言。"

    def run(self, prompt):
        os.makedirs(os.path.join(NVIDIA_HOME, "jobs"), exist_ok=True)
        jf = os.path.join(NVIDIA_HOME, "jobs", time.strftime("room-%m%d-%H%M%S.json"))
        json.dump({"messages": [{"role": "user", "content": prompt}], "max_tokens": 2048,
                   "data_classification": "public_or_synthetic"}, open(jf, "w", encoding="utf-8"), ensure_ascii=False)
        self.steps, self.started, self.said = [], time.time(), []
        self.step("輝達思考中")
        worker = _paths().get("worker")
        for attempt in range(3):  # 只有「別人正在用 worker」（還沒送出）才等一下再試；其他錯誤不重送
            self.proc = subprocess.Popen(["python", "-B", worker, jf], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            out, err = self.proc.communicate()
            self.proc = None
            out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
            if "Another worker is running" in out + err and attempt < 2:
                self.step("worker 忙，等一下")
                time.sleep(20)
                continue
            break
        try:
            info = json.loads(out.strip().splitlines()[-1])
        except Exception:
            raise RuntimeError(f"輝達 worker 沒有正常回覆：{(err or out)[-300:]}")
        text = ""
        if info.get("result_path"):
            rec = json.load(open(info["result_path"], encoding="utf-8"))
            ch = (rec.get("response") or {}).get("choices") or [{}]
            text = (ch[0].get("message", {}).get("content") or "").strip()
        if not info.get("success") or not text:
            raise RuntimeError(f"輝達呼叫失敗：{str(info.get('error'))[:300]}")
        text = nvidia_apply_settings(text)
        self.say(text)
        return text


NVIDIA_CFG_FILE = os.path.join(NVIDIA_HOME, "載入設定.json")
NVIDIA_CFG_DEFAULT = {"最近幾則": NVIDIA_CONTEXT, "字數上限": NVIDIA_CHAR_LIMIT, "略過系統通知": False}


def nvidia_cfg():
    c = dict(NVIDIA_CFG_DEFAULT)
    try:
        c.update(json.load(open(NVIDIA_CFG_FILE, encoding="utf-8")))
    except Exception:
        pass
    c["最近幾則"] = max(5, min(150, int(c["最近幾則"])))
    c["字數上限"] = max(2000, min(120000, int(c["字數上限"])))
    c["略過系統通知"] = bool(c["略過系統通知"])
    return c


def nvidia_apply_settings(text):
    """輝達在回覆最後一行寫【載入設定】…就更新它自己的載入設定（有範圍限制），並在回覆裡註明。"""
    m = re.search(r"【載入設定】([^\n]*)\s*$", text)
    if not m:
        return text
    c, changed = nvidia_cfg(), []
    for k, v in re.findall(r"(最近幾則|字數上限|略過系統通知)\s*[=＝:：]\s*([^\s，,]+)", m.group(1)):
        if k == "略過系統通知":
            c[k] = v in ("是", "true", "True", "1", "yes")
        else:
            digits = re.sub(r"\D", "", v)
            if not digits:
                continue
            c[k] = int(digits)
        changed.append(k)
    if not changed:
        return text
    os.makedirs(NVIDIA_HOME, exist_ok=True)
    json.dump(c, open(NVIDIA_CFG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    c = nvidia_cfg()
    return text[:m.start()].rstrip() + (f"\n\n（已更新我的載入設定：最近幾則 {c['最近幾則']}、字數上限 {c['字數上限']}、"
                                        f"略過系統通知 {'是' if c['略過系統通知'] else '否'}）")


# 新版輝達：會自己用工具查資料（見 nvidia_agent.py）
from nvidia_agent import NvidiaAgent  # noqa: E402,F811
