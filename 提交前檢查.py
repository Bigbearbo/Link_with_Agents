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


if __name__ == "__main__":
    sys.exit(main())
