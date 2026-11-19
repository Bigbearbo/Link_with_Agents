"""DeepSeek（OpenRouter 付費，經 H:\\Agent共用\\ai-call\\deepseek\\ds.py）討論房 agent：工具和輝達一樣用文字標記，
但有自己的家（H:\\DeepSeek用）、自己的沙盒和筆記；每次呼叫記花費，每日／每月上限由 ds.py 把關。
使用者 11-18 19:26：「deepseek 好便宜…來建立一個用用看，CLAUDE 管理」。
"""
import importlib.util, json, os, re, subprocess, time

import agents as rt
import nvidia_agent as na

HOME = r"H:\DeepSeek用"
NOTES = os.path.join(HOME, "筆記.md")
WORK = os.path.join(HOME, "work")
SHARED_OUT = r"H:\Agent共用\DeepSeek"
SANDBOX = os.path.join(HOME, ".sandbox", "run.py")
DS = r"H:\Agent共用\ai-call\deepseek\ds.py"
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-rules", "DeepSeek.md")
MAX_STEPS = 6
READ_ROOTS = na.READ_ROOTS + [HOME]


def _ds():
    spec = importlib.util.spec_from_file_location("ds", DS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GUIDE = """你是「DeepSeek」（DeepSeek V4.1 Flash，經 OpenRouter 付費額度，由 Claude 管理），多方討論房裡的 AI 參與者（其他是使用者、Claude、Codex、Gemini、輝達）。
研究主題：{topic}。用繁體中文。

你只能收發文字，但可以用「工具行」請程式幫你查資料。要用工具時，回覆裡**只寫工具行**（一行一個，最多 3 個），程式執行後把結果給你，你再決定繼續查還是發言：
【最近對話】N            這個房間最近 N 則（N≤60）
【搜尋對話】詞 [詞…]       搜尋所有房間的完整對話紀錄
【網路搜尋】問題              上網查（OpenRouter 網路搜尋，約 $0.007／次、算你的額度；必要時才用）
【讀檔】路徑 [第幾行起] [幾行]   讀檔（H:\\Agent共用、H:\\Codex用、H:\\免費模型用、H:\\Gemini用、H:\\DeepSeek用、對話紀錄；一次最多 8000 字）
【列目錄】路徑            看資料夾裡有哪些檔
【用量】                 今天／本月花了多少錢、上限多少
【寫筆記】內容            記到你自己的 H:\\DeepSeek用\\筆記.md（你的長期記憶）
【寫檔】檔名.py           （下一行起是內容，最後單獨一行寫【/寫檔】）寫到 H:\\DeepSeek用\\work；給大家看的成果寫 H:\\Agent共用\\DeepSeek\\（用完整路徑）
【續寫】檔名.py           （同上，最後寫【/續寫】）接在檔案後面寫；長程式分段寫
【跑程式】檔名.py [參數]    在沙盒裡用 Python（有 numpy）跑你工作區的腳本，最多 3 分鐘
  沙盒：只能寫 H:\\DeepSeek用、不能開子程序、不能連網、讀不到秘密檔；記憶體 2GB、CPU 25%。

規則：
- 你是**付費**的：每次呼叫都算錢（便宜，但不是免費）。一輪最多查 {steps} 步，夠回答就別多查，不要為了查而查。
- 準備好發言時，**不要寫任何工具行**，直接寫你的發言（簡潔，盡量 12 行內，只說你自己的話）。
- 不知道就說不知道，不要編造檔案內容或數字；引用要說出處（檔名或誰在幾點說的）。跑出來的數字才算數。
- 想請 Claude 協調或代做，另起一行、以 @Claude 開頭；要別人回應也一樣用行首 @名字。
- 發言裡貼程式碼用 ``` 包起來、只貼關鍵幾行；完整程式用【寫檔】存檔再說檔名。
- 不要把密碼、金鑰、帳號、學生資料寫進任何地方。
"""

TOOL_RE = re.compile(r"(?m)^[ 	]*【(最近對話|搜尋對話|網路搜尋|讀檔|列目錄|用量|寫筆記|跑程式)】[ \t]*([^【\n]*)")


GRANTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-權限.json")  # 使用者按「以後都允許」存在這（agent 自己改不到）
SHARED_ROOT = r"H:\Agent共用"
NEVER = ("ai-call", "記憶", "catalog.py", "secret", "token", "tg_state", ".dpapi", "web-key", "credential", "password", "導師助手", "待辦.json")


CODE_EXT = (".py", ".pyw", ".cmd", ".bat", ".ps1", ".js", ".vbs", ".exe", ".dll")


def granted_dirs(extra=()):
    try:
        g = json.load(open(GRANTS, encoding="utf-8")).get("DeepSeek", {}).get("可寫", [])
    except (OSError, ValueError):
        g = []
    return list(g) + list(extra)


def _in_work(name, extra=()):
    p = os.path.realpath(os.path.join(WORK, name.strip().strip('"')))
    for root in (HOME, SHARED_OUT):
        if p.lower().startswith(os.path.realpath(root).lower() + os.sep):
            return p
    if p.lower().endswith(CODE_EXT):
        return None  # Gemini 03:13：共用區其他地方不准寫程式檔（別人或中樞可能會執行）
    if os.path.basename(p).lower() == "owner.txt" and p not in [os.path.join(d, "OWNER.txt") for d in extra]:
        return None  # 別人的 OWNER.txt：就算資料夾允許過，也要每次問
    for root in granted_dirs(extra):
        if p.lower().startswith(os.path.realpath(root).lower() + os.sep):
            return p
    return None


def write_file(name, content, append=False, agent=None):
    p = _in_work(name, getattr(agent, "extra_write", ()))
    if not p:
        q = os.path.realpath(os.path.join(WORK, name.strip().strip('"')))
        if (agent is not None and q.lower().startswith(os.path.realpath(SHARED_ROOT).lower() + os.sep)
                and not any(b in q.lower() for b in NEVER) and not q.lower().endswith(CODE_EXT)):
            # 共用區其他地方：記下來，這輪結束後請使用者同意（允許一次／以後都允許這個資料夾）
            d = os.path.dirname(q)
            agent.denials = getattr(agent, "denials", []) or []
            if not any(x.get("dir") == d for x in agent.denials if isinstance(x, dict)):
                agent.denials.append({"kind": "寫入", "dir": d})
            return f"還不能寫：{q}。已向使用者申請寫入「{d}」，同意後下一輪再寫一次。"
        return f"不能寫：{name}（只能寫 H:\\DeepSeek用、H:\\Agent共用\\DeepSeek，和使用者允許過的資料夾）"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content + "\n")
    return f"已{'續寫' if append else '寫入'} {p}（這次 {len(content)} 字，檔案共 {os.path.getsize(p)} 位元組）"


def run_script(arg):
    parts = arg.split()
    p = _in_work(parts[0]) if parts else None
    if not p or not p.endswith(".py") or not os.path.isfile(p):
        return f"找不到或不能跑：{arg}（要是工作區裡的 .py）"
    spec = importlib.util.spec_from_file_location("ds_limits", os.path.join(HOME, ".sandbox", "limits.py"))
    lim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lim)
    os.makedirs(WORK, exist_ok=True)
    proc = subprocess.Popen([na.SANDBOX_PY, "-B", "-X", "utf8", SANDBOX, p, *parts[1:]], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=WORK, creationflags=rt.NO_WINDOW,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    job = lim.assign(proc)
    try:
        out, err = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return "超過 3 分鐘被停掉"
    finally:
        lim.close(job)
    out = (out + b"\n" + err).decode("utf-8", "replace").strip()
    note = "" if job else "\n（警告：資源限制沒套上）"
    if "沙盒：" in out or "MemoryError" in out:
        note += "\n（被沙盒或資源上限擋下；真的需要就在發言裡寫 @Claude 申請，不要想辦法繞過。）"
    return f"結束碼 {proc.returncode}\n" + (out[-6000:] or "（沒有輸出）") + note


def usage_text():
    try:
        ds = _ds()
        d, m = ds.spent()
        c = ds.conf()
        return f"DeepSeek 今天花了 ${d:.4f}（上限 ${c['每日上限美元']}），本月 ${m:.4f}（上限 ${c['每月上限美元']}）。"
    except Exception as e:
        return f"（花費帳讀不到：{type(e).__name__}）"


def run_tool(kind, arg, transcript, agent=None):
    arg = arg.strip()
    if kind == "跑程式":
        return run_script(arg)
    if kind == "用量":
        return usage_text()
    if kind == "網路搜尋":  # 11-19：改用 OpenRouter web 外掛（約 $0.007／次，記帳本），不再借 agy
        try:
            return _ds().web_search(arg, who="DeepSeek")
        except Exception as e:
            return f"搜尋失敗：{type(e).__name__} {str(e)[:200]}"
    if kind == "寫筆記":
        if not arg:
            return "筆記是空的"
        with open(NOTES, "a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%m-%d %H:%M')}\n{arg[:4000]}\n")
        return "已記下"
    if kind in ("讀檔", "列目錄"):
        path = (arg.split() or [""])[0]
        p = os.path.realpath(path).lower() if path else ""
        if p.startswith(os.path.realpath(HOME).lower()) and not any(b in p for b in na.BLOCK):
            # 自己家：na._allowed 不認得 H:\DeepSeek用，這裡直接處理
            if kind == "列目錄":
                if not os.path.isdir(path):
                    return f"不能列：{path}"
                return "\n".join(n + ("/" if os.path.isdir(os.path.join(path, n)) else "") for n in sorted(os.listdir(path))[:200])
            if not os.path.isfile(path):
                return f"不能讀：{path}"
            return open(path, encoding="utf-8", errors="replace").read()[:na.READ_LIMIT]
    if kind in ("最近對話", "搜尋對話", "網路搜尋", "讀檔", "列目錄"):
        return na.run_tool(kind, arg, transcript, agent)
    return "不認得的工具"


def big_brother_rules():
    try:
        return "\n\n【大哥給你的規則（必須遵守）】\n" + open(RULES_FILE, encoding="utf-8").read()[:6000] + "\n"
    except OSError:
        return ""


class DeepSeekAgent(rt.Agent):
    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass

    def build_prompt(self, transcript):
        self._transcript = list(transcript)
        # 使用者 11-19 09:07：「共用區有變動」不要整串塞給它，改照它自己的條件篩（變動條件.json），想看更多再改條件或自己查
        new = [m for m in transcript[self.seen:] if not (m["who"] == "系統" and m["text"].startswith("共用區有變動"))][-8:]
        body = "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in new)
        return (GUIDE.format(steps=MAX_STEPS, topic=rt.RESEARCH_TOPIC or "（見使用者說明）") + big_brother_rules()
                + f"\n現在時間 {time.strftime('%m-%d %H:%M')}。{usage_text()}\n\n"
                f"自從你上次發言後的新訊息（最多列最後 8 則；更早的請用工具查）：\n\n{body}"
                + self.changes_digest()
                + rt.work_memory("DeepSeek", body) + "\n\n現在輪到你（DeepSeek）。")

    CHANGE_RULE = os.path.join(HOME, "訂閱.json")  # 使用者 09:08：訂閱資訊（原名 變動條件.json）

    def changes_digest(self):
        """依 H:\\DeepSeek用\\變動條件.json 篩上次醒來後的共用區變動。檔案格式（DeepSeek 自己改）：
        {"關鍵字": ["DeepSeek", "AI評比", "課程"], "類": "", "最多": 10}；關鍵字是「任一個符合就列」，空的＝全部。"""
        import shared_watch as sw
        try:
            rule = json.load(open(self.CHANGE_RULE, encoding="utf-8"))
        except (OSError, ValueError):
            rule = {"關鍵字": ["DeepSeek", "AI評比", "團隊目標", "待辦"], "類": "", "最多": 10}
        since = getattr(self, "_changes_since", "") or time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 6 * 3600))
        self._changes_since = time.strftime("%Y-%m-%d %H:%M")
        kws = rule.get("關鍵字") or [""]
        lines = sw.digest(rule, since)   # 跟視窗 agent 的訂閱摘要同一個函式
        return (f"\n\n共用區變動（你的訂閱 `{self.CHANGE_RULE}`：關鍵字 {kws}；想看更多就改那個檔，"
                f"或用【讀檔】看 `H:\\Claude用\\三方討論\\logs\\共用區變動.jsonl`）：\n" + (lines or "（沒有符合的）"))

    def _call(self, messages):
        try:
            text, cost, mid = _ds().chat(messages, model="flash", max_tokens=8192, who="DeepSeek")  # 11-19 02:03 finish=length 空白 → 加大
        except RuntimeError as e:
            if "空白" not in str(e):
                raise
            self.step("回了空白，重試一次")  # 11-19 03:30 finish=stop 也會空白（供應商偶發）；重試一次，再空白才算失敗
            text, cost, mid = _ds().chat(messages, model="flash", max_tokens=8192, who="DeepSeek")
        self.last_model = mid
        self.step(f"花 ${cost:.4f}")
        return text

    def run(self, prompt):
        self.steps, self.started, self.said = [], time.time(), []
        self.extra_write = getattr(self, "next_extra_write", [])  # 「允許一次」只給這一輪
        self.next_extra_write = []
        transcript = getattr(self, "_transcript", [])
        messages = [{"role": "user", "content": prompt}]
        auto = "自主工作時段" in prompt
        limit = 10 if auto else MAX_STEPS
        for step in range(limit + 1):
            self.step("DeepSeek 思考中" if step == 0 else f"DeepSeek 第 {step + 1} 步")
            text = self._call(messages)
            writes = na.WRITE_RE.findall(text)[:3]
            cut = False
            if not writes:
                m = na.OPEN_RE.search(text)
                if m and step < limit:
                    writes, cut = [m.groups()], True
            tools = [] if cut else TOOL_RE.findall(na.WRITE_RE.sub("", text))[:3]
            if not (tools or writes) or step == limit:
                final = TOOL_RE.sub("", na.WRITE_RE.sub("", text)).strip() if (tools or writes) else text
                final = final or "（我查完了資料但沒有整理出發言。）"
                self.say(final)
                return final
            results = []
            for tag, name, content in writes:
                self.step(f"{tag}：{name[:40]}")
                try:  # 11-19 03:07：檔名寫壞（WinError 123）曾讓整輪失敗；改成只回報這一筆
                    res = write_file(name, content, append=(tag == '續寫'), agent=self)
                except Exception as e:
                    res = f"寫檔出錯：{type(e).__name__} {e}（檔名要是單純路徑，不要加反引號或說明文字）"
                results.append(f"【{tag}】{name}（已執行）\n{res}")
            if cut:
                results.append("⚠ 你的回覆太長被截斷了：上面這個檔只存到截斷處。請用【續寫】同一個檔名接著寫，最後【/續寫】收尾。")
            for kind, arg in tools:
                self.step(f"{kind}：{arg[:40]}")
                try:
                    res = run_tool(kind, arg, transcript, self)
                except Exception as e:
                    res = f"工具出錯：{e}"
                results.append(f"【{kind}】{arg}（已執行）\n{res}")
            left = limit - step - 1
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "工具結果：\n\n" + "\n\n---\n\n".join(results) +
                          (f"\n\n（還可以查 {left} 步；夠了就直接發言，不要再寫工具行。）" if left else
                           "\n\n（查詢步數用完了，現在請直接發言，不要再寫工具行。）")}]
