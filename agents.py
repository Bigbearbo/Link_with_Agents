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

WORKDIR = os.path.abspath(os.environ.get("LINK_WITH_WORKDIR", os.getcwd()))
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PREAMBLE = (
    "【三方討論規則】你正在一個三方討論視窗裡，參與者：使用者、Claude（Anthropic 的 Claude Code）、"
    "Codex（OpenAI 的 Codex）。使用者可能用電腦網頁或手機 Telegram 發言，兩邊是同一串討論。\n"
    f"- 共用工作區是 {WORKDIR}。請先讀該工作區的 AGENTS.md（若有）。\n"
    "{perm}"
    "- 用繁體中文回答，簡潔，盡量 15 行內，有根據時附檔案路徑。\n"
    "- 你會收到其他參與者的新發言，格式是【發言者】內容。可以同意、反駁或補充對方。\n"
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
WORKDIR_NOTE = f"【工作區設定】目前共用工作區是 {WORKDIR}；後續讀寫依使用者指示及該工作區的規則。\n"


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
        self.denials = []      # 上一輪被權限擋下的動作
        self.extra_allow = []  # 使用者允許一次的規則
        self.pending = False   # 忙碌中又有新訊息：做完再回一輪

    def step(self, text):
        self.steps.append(short(text))
        del self.steps[:-8]

    def build_prompt(self, transcript):
        new = transcript[self.seen:]
        body = "\n\n".join(f"【{m['who']}】{m['text']}" for m in new if m["who"] != self.name)
        if not body:
            body = "（沒有新發言，請繼續補充或回應目前的討論。）"
        head = PREAMBLE.replace("{perm}", PERM_RW if self.write else PERM_RO) if self.session is None else ""
        return head + body + f"\n\n現在輪到你（{self.name}）發言。"

    def _spawn(self, cmd, prompt):
        if not cmd[0]:
            raise RuntimeError(f"找不到 {self.name} 的程式，請確認已安裝")
        self.steps, self.started = [], time.time()
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
        if self.session:
            cmd += ["--resume", self.session]
        err = self._spawn(cmd, prompt)
        result = None
        for ev in self._lines():
            if ev.get("session_id"):
                self.session = ev["session_id"]
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        what = (inp.get("file_path") or inp.get("command") or inp.get("pattern")
                                or inp.get("path") or inp.get("url") or "")
                        self.step(f"{self.TOOL_LABEL.get(c.get('name'), c.get('name'))}：{what}")
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        self.step("撰寫回覆…")
            elif ev.get("type") == "result":
                result = ev
        rc, errtext = self._finish(err)
        if result is None:
            raise RuntimeError(f"Claude 沒有回覆（exit {rc}）：{errtext[-500:]}")
        self.denials = [d for d in (result.get("permission_denials") or []) if isinstance(d, dict)]
        if result.get("is_error"):
            raise RuntimeError(f"Claude 錯誤：{result.get('result')}")
        return (result.get("result") or "").strip()


class CodexAgent(Agent):
    def run(self, prompt):
        mode = "workspace-write" if self.write else "read-only"
        if self.session:
            cmd = [CODEX_EXE, "exec", "resume", self.session, "--json", "--skip-git-repo-check",
                   "-c", f'sandbox_mode="{mode}"', "-"]
        else:
            cmd = [CODEX_EXE, "exec", "--json", "--skip-git-repo-check", "-s", mode, "-C", WORKDIR, "-"]
        err = self._spawn(cmd, prompt)
        msgs, errors = [], []
        for ev in self._lines():
            t, item = ev.get("type"), ev.get("item") or {}
            it = item.get("type")
            if t == "thread.started":
                self.session = ev.get("thread_id") or self.session
            elif t == "item.started" and it == "command_execution":
                c = str(item.get("command", ""))
                c = re.sub(r'^.*?(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?)"?\s+(?:-NoProfile\s+)?(?:-Command|/c)\s+', "", c, flags=re.I)
                self.step(f"執行：{c.strip(chr(34) + chr(39))}")
            elif t == "item.completed":
                if it == "agent_message":
                    msgs.append(item.get("text", ""))
                    self.step("撰寫回覆…")
                elif it == "file_change":
                    paths = ", ".join(os.path.basename(c.get("path", "")) for c in item.get("changes") or [])
                    self.step(f"修改：{paths}")
                elif it == "reasoning" and item.get("text"):
                    self.step(f"思考：{item['text']}")
                elif it in ("web_search", "mcp_tool_call"):
                    self.step(f"{'搜尋網路' if it == 'web_search' else '工具'}：{item.get('query') or item.get('tool', '')}")
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
