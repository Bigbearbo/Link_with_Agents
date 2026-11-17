"""輝達（OpenRouter 免費模型）討論房 agent：用文字標記自己叫工具（讀對話、搜紀錄、讀檔、看用量、寫筆記），自己決定要讀多少。

模型本身只能收發文字；這裡把它回覆中的【工具】行拿來執行（只讀、限定資料夾、有長度上限），把結果再送回去，
最多 MAX_STEPS 步；沒有工具行的那一則就是它在房間的發言。
"""
import datetime, json, os, re, subprocess, time

import agents as rt

HOME = rt.NVIDIA_HOME
NOTES = os.path.join(HOME, "筆記.md")
CALLS = os.path.join(HOME, "calls.jsonl")
LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
SEARCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search.py")
READ_ROOTS = [r"H:\Agent共用", r"H:\Codex用", r"H:\免費模型用", r"H:\Gemini用", LOGS]
BLOCK = ("secret", "token", "tg_state", ".dpapi", "web-key", "credential", "password", "金鑰")
MAX_STEPS = 6
READ_LIMIT = 8000
DAILY_SOFT = 300  # 輝達自己在討論房一天最多用幾次（worker 另有全體 900／保留 100）

GUIDE = """你是「輝達」（NVIDIA Nemotron，經 OpenRouter 免費額度），多方討論房裡的 AI 參與者（其他是使用者、Claude、Codex、Gemini）。
研究主題：{topic}。用繁體中文。

你只能收發文字，但可以用「工具行」請程式幫你查資料。要用工具時，回覆裡**只寫工具行**（一行一個，最多 3 個），程式執行後把結果給你，你再決定繼續查還是發言：
【最近對話】N            這個房間最近 N 則（N≤60）
【搜尋對話】詞 [詞…]       搜尋所有房間的完整對話紀錄（多個詞＝都要出現；想找「A 或 B」就分兩行查）
【網路搜尋】問題              上網查（借 Gemini Flash 的 Google 搜尋，回摘要＋網址；較慢，必要時才用）
【讀檔】路徑 [第幾行起] [幾行]   讀檔（只限 H:\\Agent共用、H:\\Codex用、H:\\免費模型用、H:\\Gemini用、對話紀錄；一次最多 8000 字）
【列目錄】路徑            看資料夾裡有哪些檔
【用量】                 你今天用了幾次、整體免費額度還剩多少
【寫筆記】內容            記到你自己的 H:\\免費模型用\\筆記.md（下次可以讀回來，當你的長期記憶）
你用哪個模型由你自己決定：OpenRouter 上任何 `:free` 結尾的免費模型都可以（例如 nvidia/…、meta-llama/…、qwen/…、deepseek/… 的 :free 版）。
  把模型 ID 寫在 H:\\免費模型用\\模型.txt 第一行（用【寫檔】../模型.txt），下一次呼叫生效；不是 :free 結尾或不寫，就用預設的 Nemotron。
  不同題目可以換不同模型（例如寫程式、推理、長文），自己記在筆記裡哪個好用；筆記按模型分開，單次成功只算候選經驗。
  每次呼叫的「要求模型／實際回傳模型／是否退回預設」記在 H:\免費模型用\calls.jsonl，評估模型時以實際回傳的為準。
【寫檔】檔名.py           （下一行起是檔案內容，最後單獨一行寫【/寫檔】）寫到你的工作區 H:\\免費模型用\\work（只能寫這裡）
【跑程式】檔名.py [參數]    在沙盒裡用 Python（有 numpy）跑你工作區的腳本，最多 3 分鐘，回傳輸出的最後 6000 字
  沙盒限制：只能寫 H:\\免費模型用、不能開子程序、不能連網、讀不到秘密檔；資源上限：記憶體 2GB、CPU 25%、3 分鐘。可以讀共用區和 H:\\Codex用 的資料來算。
  在這些範圍內你可以自己做主，不用先問；超出範圍（更多資源、寫別處、跑更久）才在發言裡寫 @Claude 申請，說明要做什麼、為什麼。

規則：
- 一輪最多查 {steps} 步，用完就必須發言；額度有限，夠回答就別多查。
- 準備好發言時，**不要寫任何工具行**，直接寫你的發言（簡潔，盡量 12 行內，只說你自己的話）。
- 不知道就說不知道，不要編造檔案內容或數字；引用要說出處（檔名或誰在幾點說的）。
- 想跟 Claude 協調或請它代做，**另起一行、以 @Claude 開頭**寫你的請求（只有行首的 @Claude 會叫醒它；句子中間提到不算）。
- 你只能在沙盒裡跑自己工作區的 Python 腳本；系統指令、改別人的檔、啟停訓練都不行，需要就請 @Claude。別承諾做不到的事。
- 跑出來的數字才算數；程式出錯就讀錯誤訊息修正再跑，不要假裝跑成功。
- 說「誰做了什麼」之前要有出處：系統的「共用區有變動」通知裡，括號是該資料夾的負責人，不是發通知的人。
- 每天第一次被叫到時：先【讀檔】H:\免費模型用\筆記.md，若今天還沒有「OpenRouter 規則檢查」，就用【網路搜尋】查一次免費模型的額度規則有沒有變，結果用【寫筆記】記下（標題寫「OpenRouter 規則檢查 日期」）。
"""

TOOL_RE = re.compile(r"【(最近對話|搜尋對話|網路搜尋|讀檔|列目錄|用量|寫筆記|跑程式)】[ \t]*([^【\n]*)")
WRITE_RE = re.compile(r"【寫檔】[ \t]*([^\n【]+)\n(.*?)\n?【/寫檔】", re.S)
WORK = os.path.join(HOME, "work")
SANDBOX = os.path.join(HOME, ".sandbox", "run.py")
SANDBOX_PY = r"D:\GPT-Cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"  # 有 numpy


def _in_work(name):
    p = os.path.realpath(os.path.join(WORK, name.strip().strip('"')))
    return p if p.lower().startswith(os.path.realpath(HOME).lower() + os.sep) else None


def write_file(name, content):
    p = _in_work(name)
    if not p:
        return f"不能寫：{name}（只能寫 H:\\免費模型用 裡）"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(content)
    return f"已寫入 {p}（{len(content)} 字）"


def run_script(arg):
    parts = arg.split()
    p = _in_work(parts[0]) if parts else None
    if not p or not p.endswith(".py") or not os.path.isfile(p):
        return f"找不到或不能跑：{arg}（要是工作區裡的 .py）"
    import importlib.util
    spec = importlib.util.spec_from_file_location("nv_limits", os.path.join(HOME, ".sandbox", "limits.py"))
    lim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lim)
    proc = subprocess.Popen([SANDBOX_PY, "-B", "-X", "utf8", SANDBOX, p, *parts[1:]], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=WORK, creationflags=rt.NO_WINDOW,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    job = lim.assign(proc)  # 記憶體 2GB、CPU 25%、只准一個行程、低優先
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
        note += ("\n（被沙盒或資源上限擋下。如果真的需要更多權限或資源，在發言裡寫 @Claude 申請，"
                 "說明要做什麼、為什麼；不要想辦法繞過。）")
    return f"結束碼 {proc.returncode}\n" + (out[-6000:] or "（沒有輸出）") + note


def _allowed(path):
    p = os.path.realpath(path)
    if any(b in p.lower() for b in BLOCK):
        return False
    return any(p.lower().startswith(os.path.realpath(r).lower()) for r in READ_ROOTS)


def _today_calls():
    d, n = time.strftime("%Y-%m-%d"), 0
    try:
        for line in open(CALLS, encoding="utf-8"):
            n += line.startswith('{"date": "' + d)
    except OSError:
        pass
    return n


def _log_call(ok, requested=None, actual=None):
    """每次呼叫記一行：要求的模型、實際回傳的模型、是否退回預設（Codex 11-17：評測不能把預設模型的答案算到指定模型）。"""
    os.makedirs(HOME, exist_ok=True)
    with open(CALLS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": time.strftime("%Y-%m-%d"), "time": time.strftime("%H:%M:%S"), "ok": ok,
                            "requested": requested or "（預設）", "actual": actual,
                            "fallback": bool(requested and actual and not str(actual).startswith(requested.split(":")[0]))},
                           ensure_ascii=False) + "\n")


def usage_text():
    used = _today_calls()
    txt = f"你今天在討論房用了 {used} 次（自訂上限 {DAILY_SOFT}）。"
    try:
        b = json.load(open(rt._paths().get("budget", r"D:\Codex用\work\openrouter-worker\budget.json"), encoding="utf-8"))
        utc = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        all_used = b.get("attempts", 0) if b.get("day") == utc else 0
        txt += f" 整個 OpenRouter 免費額度今天（UTC 日，台北 08:00 換日）已用 {all_used}/900（900 是 worker 上限，已經扣掉保留給使用者的 100；這個數字以 worker 紀錄為準）。"
    except Exception:
        txt += " （整體額度檔讀不到）"
    return txt


def run_tool(kind, arg, transcript):
    arg = arg.strip()
    if kind == "最近對話":
        n = max(1, min(60, int(re.sub(r"\D", "", arg) or 10)))
        return "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in transcript[-n:])[-20000:]
    if kind == "搜尋對話":
        arg = re.sub(r"^關鍵字[:：]?\s*", "", arg)
        if not arg:
            return "請給關鍵字"
        r = subprocess.run(["python", "-B", SEARCH, *arg.split()[:4], "--limit", "8", "--context", "150"],
                           capture_output=True, timeout=60, creationflags=rt.NO_WINDOW,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return r.stdout.decode("utf-8", "replace")[-12000:] or "（沒有找到）"
    if kind == "網路搜尋":
        return web_search(arg)
    if kind == "跑程式":
        return run_script(arg)
    if kind == "讀檔":
        parts = arg.split()
        path = parts[0] if parts else ""
        start = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 200
        if not path or not os.path.isfile(path) or not _allowed(path):
            return f"不能讀：{path}（不存在、不是檔案，或不在允許的資料夾）"
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        chunk = "\n".join(lines[start - 1:start - 1 + count])
        more = f"\n……（共 {len(lines)} 行，這次給第 {start}–{min(len(lines), start + count - 1)} 行" + \
               ("，被 8000 字截斷" if len(chunk) > READ_LIMIT else "") + "）"
        return chunk[:READ_LIMIT] + more
    if kind == "列目錄":
        if not os.path.isdir(arg) or not _allowed(arg):
            return f"不能列：{arg}"
        names = sorted(os.listdir(arg))[:200]
        return "\n".join(n + ("/" if os.path.isdir(os.path.join(arg, n)) else "") for n in names)
    if kind == "用量":
        return usage_text()
    if kind == "寫筆記":
        if not arg:
            return "筆記是空的"
        os.makedirs(HOME, exist_ok=True)
        with open(NOTES, "a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%m-%d %H:%M')}\n{arg[:4000]}\n")
        return "已記下"
    return "不認得的工具"


def web_search(q):
    """借 Gemini 3.8 Flash（agy，Google 搜尋）幫輝達上網查；不給任何檔案權限。"""
    if not q:
        return "請給要查的問題"
    r = subprocess.run([rt.AGY_EXE, "-p", f"用網路搜尋回答，繁體中文，8 行內，每點附來源網址；查不到就說查不到：{q[:500]}",
                        "--model", "gemini-3.8-flash-low", "--sandbox", "--output-format", "json", "--print-timeout", "120s"],
                       capture_output=True, timeout=180, cwd=HOME, creationflags=rt.NO_WINDOW)
    try:
        return (json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1]).get("response") or "（沒有結果）")[:6000]
    except Exception:
        return "網路搜尋失敗"


MODEL_FILE = os.path.join(HOME, "模型.txt")
FREE_MODEL = re.compile(r"[a-z0-9][\w.\-]*/[\w.\-]+:free")


def chosen_model():
    """它自己決定用哪個 OpenRouter 免費模型：寫在 H:\\免費模型用\\模型.txt 第一行（必須是 :free 結尾），
    其他或不寫就用 worker 預設。worker 本身還會再核對免費目錄與零價格。"""
    try:
        first = open(MODEL_FILE, encoding="utf-8-sig").readline().strip()
    except OSError:
        return None
    return first if FREE_MODEL.fullmatch(first) else None


class NvidiaAgent(rt.Agent):
    @property
    def write(self):
        return False

    @write.setter
    def write(self, v):
        pass

    def build_prompt(self, transcript):
        self._transcript = list(transcript)
        new = transcript[self.seen:][-8:]  # 只先給最近的新訊息；要更多它自己查
        body = "\n\n".join(f"【{m['who']}｜{m.get('time', '')}】{m['text']}" for m in new)
        return (GUIDE.format(steps=MAX_STEPS, topic=rt.RESEARCH_TOPIC or "（見使用者說明）") +f"\n現在時間 {time.strftime('%m-%d %H:%M')}。{usage_text()}\n\n"
                f"自從你上次發言後的新訊息（最多列最後 8 則；更早的請用工具查）：\n\n{body}\n\n現在輪到你（輝達）。")

    def _call(self, messages):
        if _today_calls() >= DAILY_SOFT:
            raise RuntimeError(f"輝達今天在討論房已用滿 {DAILY_SOFT} 次")
        os.makedirs(os.path.join(HOME, "jobs"), exist_ok=True)
        jf = os.path.join(HOME, "jobs", time.strftime("room-%m%d-%H%M%S.json"))
        spec = {"messages": messages, "max_tokens": 2048, "data_classification": "public_or_synthetic"}
        model = chosen_model()
        if model:
            spec["model"] = model
            self.step(f"模型：{model}")
        json.dump(spec, open(jf, "w", encoding="utf-8"), ensure_ascii=False)
        worker = rt._paths().get("worker")
        for attempt in range(3):  # 只有 worker 被別人佔用（還沒送出）才等一下再試；其他錯誤不重送
            self.proc = subprocess.Popen(["python", "-B", worker, jf], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, creationflags=rt.NO_WINDOW)
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
            _log_call(False)
            raise RuntimeError(f"輝達 worker 沒有正常回覆：{(err or out)[-300:]}")
        text, actual = "", None
        if info.get("result_path"):
            rec = json.load(open(info["result_path"], encoding="utf-8"))
            ch = (rec.get("response") or {}).get("choices") or [{}]
            text = (ch[0].get("message", {}).get("content") or "").strip()
            actual = (rec.get("response") or {}).get("model") or rec.get("model")
        _log_call(bool(info.get("success") and text), spec.get("model"), actual)
        self.last_model = actual
        if not info.get("success") or not text:
            raise RuntimeError(f"輝達呼叫失敗：{str(info.get('error'))[:300]}")
        return text

    def run(self, prompt):
        self.steps, self.started, self.said = [], time.time(), []
        transcript = getattr(self, "_transcript", [])
        messages = [{"role": "user", "content": prompt}]
        for step in range(MAX_STEPS + 1):
            self.step("輝達思考中" if step == 0 else f"輝達第 {step + 1} 步")
            text = self._call(messages)
            writes = WRITE_RE.findall(text)[:3]
            tools = TOOL_RE.findall(WRITE_RE.sub("", text))[:3]
            if not (tools or writes) or step == MAX_STEPS:
                final = TOOL_RE.sub("", WRITE_RE.sub("", text)).strip() if (tools or writes) else text
                final = final or "（我查完了資料但沒有整理出發言。）"
                self.say(final)
                return final
            results = []
            for name, content in writes:  # 先寫檔，同一則裡的【跑程式】才跑得到新內容
                self.step(f"寫檔：{name[:40]}")
                results.append(f"【寫檔】{name}（已執行）\n{write_file(name, content)}")
            for kind, arg in tools:
                self.step(f"{kind}：{arg[:40]}")
                try:
                    res = run_tool(kind, arg, transcript)
                except Exception as e:
                    res = f"工具出錯：{e}"
                results.append(f"【{kind}】{arg}（已執行）\n{res}")
            left = MAX_STEPS - step - 1
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "工具結果：\n\n" + "\n\n---\n\n".join(results) +
                          (f"\n\n（還可以查 {left} 步；夠了就直接發言，不要再寫工具行。）" if left else
                           "\n\n（查詢步數用完了，現在請直接發言，不要再寫工具行。）")}]
