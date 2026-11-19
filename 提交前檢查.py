r"""提交前檢查（git pre-commit 呼叫）：把「反斜線陷阱」變成不可能犯。
來源：P-stack 的 /correct 原則（使用者 11-20 02:07 請大家研究）——重複犯的錯，不寫筆記提醒，改成程式擋住。
這個錯已經發生十幾次：heredoc／非 raw 字串把 Windows 路徑的 \a \b \0 變成控制字元、把 \n 變成真的換行，
檔案照樣存進去，等執行才壞。

檢查暫存區（staged）的檔案：
  1. .py .cmd .ps1 .md .json .html .txt 不能含控制字元（Tab、換行、Windows 換行除外）
  2. .py 必須能編譯（py_compile）
  3. .json 必須能解析
擋下時印出：哪個檔、第幾行、是什麼字元、怎麼修。要硬過（確定是故意的）：git commit --no-verify，並在訊息寫原因。
"""
import json, os, py_compile, subprocess, sys, tempfile

BAD = {c for c in range(32) if c not in (9, 10, 13)}
TEXT = (".py", ".cmd", ".bat", ".ps1", ".md", ".json", ".html", ".txt", ".js", ".css")
FIX = "多半是 Windows 路徑的反斜線被當成跳脫字元：改用 Edit 工具或 r\"...\" 原始字串，別用 heredoc 塞路徑。"


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
                         capture_output=True).stdout.decode("utf-8", "replace")
    return [f for f in out.split("\0") if f]


def blob(path):
    return subprocess.run(["git", "show", f":{path}"], capture_output=True).stdout


def main():
    errs = []
    for f in staged():
        if not f.lower().endswith(TEXT):
            continue
        data = blob(f)
        text = data.decode("utf-8", "replace")
        for n, line in enumerate(text.split("\n"), 1):
            hit = [c for c in line if ord(c) in BAD]
            if hit:
                errs.append(f"{f}:{n} 有控制字元 {', '.join(repr(c) for c in sorted(set(hit)))} → {FIX}")
                break
        if f.lower().endswith(".py"):
            with tempfile.NamedTemporaryFile("wb", suffix=".py", delete=False) as t:
                t.write(data)
            try:
                py_compile.compile(t.name, doraise=True)
            except py_compile.PyCompileError as e:
                msg = str(e).replace(t.name, f).splitlines()[-1]
                errs.append(f"{f} 編譯失敗：{msg}（常見原因：字串裡的 \\n 被寫成真的換行 → {FIX}）")
            finally:
                os.unlink(t.name)
        if f.lower().endswith(".json"):
            try:
                json.loads(text)
            except ValueError as e:
                errs.append(f"{f} JSON 壞了：{e}")
    if errs:
        print("✋ 提交前檢查擋下（把重複犯的錯變成不可能犯）：", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        print("確定是故意的才用 git commit --no-verify，並在提交訊息寫原因。", file=sys.stderr)
        return 1
    return 0


# ---- 自動修（使用者 11-20 02:10：用「方言轉換」的概念，不只檢查，能確定的就自動修）----
# 反斜線陷阱是一種固定的「轉譯錯誤」：原文的 \a \b \f \v \0 被跳脫成單一控制字元。
# 這個對應是一對一的，所以可以反轉回去；修完一定要再編譯／解析一次，過了才寫回，不過就保留原檔並回報。
UNDO = {"\x07": "\\a", "\x08": "\\b", "\x0c": "\\f", "\x0b": "\\v", "\x00": "\\0", "\x1b": "\\x1b"}


# 使用者 11-20 04:08（待討論 #30）：「可以直接修正，你寫好轉換規則，但要記得有被修正（清單介紹修正規則），需要時能去看；
# 報錯是更嚴重的異常（程式處理不了的模糊區但能偵測），最後是執行時被擋下」
# → 三層：①規則能確定就自動修＋留紀錄 ②偵測得到但原意不確定就報錯 ③都沒抓到的，執行時被擋下（py 編譯／JSON 解析失敗）。
# 規則清單：H:\Agent共用\修正規則.md；每次自動修都寫一筆 logs\自動修正.jsonl（哪個檔、哪條規則、改了什麼）。
FIX_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "自動修正.jsonl")


def record(path, rule, what):
    try:
        os.makedirs(os.path.dirname(FIX_LOG), exist_ok=True)
        import time as _t
        with open(FIX_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"時間": _t.strftime("%Y-%m-%d %H:%M:%S"), "檔": os.path.abspath(path), "規則": rule, "改了": what}, ensure_ascii=False) + chr(10))
    except OSError:
        pass


def fix_file(path, write=False):
    """回傳 (狀態, 說明)。狀態：ok（本來就好）、fixed（修好）、would（試修可行但沒寫）、fail（修不了）。"""
    raw = open(path, "rb").read()
    if path.lower().endswith(".json"):   # JSON 的 BOM、尾逗號：接 Codex 11-20 02:12 的 repair_json_format.normalize（不重寫）
        try:
            json.loads(raw.decode("utf-8"))
        except ValueError:
            try:
                import importlib.util as _u
                sp = _u.spec_from_file_location("rjf", r"H:\Codex用\outputs\roundtable-updates\repair_json_format.py")
                rjf = _u.module_from_spec(sp)
                sp.loader.exec_module(rjf)
                cand, fixes = rjf.normalize(raw)
                if fixes:
                    if write:
                        open(path, "wb").write(cand)
                        record(path, "R3／R4 JSON 修復", "、".join(fixes))
                    return ("fixed" if write else "would"), "、".join(fixes) + "（Codex 的 JSON 修復）"
            except Exception:
                pass   # 修不了就往下走控制字元反轉
    text = raw.decode("utf-8", "replace")
    hits = [c for c in text if c in UNDO]
    other = [c for c in text if ord(c) in BAD and c not in UNDO]
    if not hits:
        return ("fail", f"有其他控制字元 {sorted(set(map(repr, other)))}，原意不確定，不自動修") if other else ("ok", "沒問題")
    new = "".join(UNDO.get(c, c) for c in text)
    if path.lower().endswith(".py"):
        try:
            compile(new, path, "exec")
        except SyntaxError as e:
            return "fail", f"反轉後還是編譯不過（{e.msg}，第 {e.lineno} 行），保留原檔"
        # Codex 02:14：能編譯不代表路徑對——一般 "..." 字串裡的 \a 執行時又會變回控制字元。
        # 只有每個修補點都落在 r"..." 原始字串裡才自動修；否則報行號、保留原檔。
        import io, tokenize
        spots, line, col = [], 1, 0
        for c in text:
            if c in UNDO:
                spots.append((line, col))
            if c == "\n":
                line, col = line + 1, 0
            else:
                col += len(UNDO.get(c, c))
        raw_ok = set()
        for tok in tokenize.generate_tokens(io.StringIO(new).readline):
            if tok.type == tokenize.STRING:
                prefix = tok.string[:tok.string.find(tok.string.lstrip("rRbBuUfF")[0])].lower()
                for s in spots:
                    if tok.start <= s < tok.end and "r" in prefix:
                        raw_ok.add(s)
        unsure = sorted({s[0] for s in spots if s not in raw_ok})
        if unsure:
            return "fail", f"第 {'、'.join(map(str, unsure))} 行的修補點不在 r\"...\" 原始字串裡，修回 \\a 執行時仍會壞，原意不確定，保留原檔"
    if path.lower().endswith(".json"):
        try:
            json.loads(new)
        except ValueError as e:
            return "fail", f"反轉後 JSON 還是壞的（{e}），保留原檔"
    desc = "、".join(f"{repr(c)}→{UNDO[c]} ×{hits.count(c)}" for c in sorted(set(hits)))
    if write:
        open(path, "w", encoding="utf-8", newline="").write(new)
        record(path, "R1 反斜線陷阱反轉", desc)
        return "fixed", desc
    return "would", desc


if __name__ == "__main__":
    if sys.argv[1:2] == ["修"]:   # PY 提交前檢查.py 修 檔案... [--寫]
        w = "--寫" in sys.argv
        bad = 0
        for f in [a for a in sys.argv[2:] if a != "--寫"]:
            st, msg = fix_file(f, w)
            bad += st == "fail"
            print({"ok": "✅", "fixed": "🔧 已修", "would": "🔍 可修（加 --寫 才寫回）", "fail": "❌"}[st], f, msg)
        sys.exit(1 if bad else 0)
    sys.exit(main())
