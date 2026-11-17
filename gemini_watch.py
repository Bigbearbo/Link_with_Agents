"""Gemini 長期研究觀察：每天一次，用 Gemini 的網路搜尋追蹤 H:\Gemini用\觀察主題.md 的主題，
結果寫 H:\Gemini用\研究觀察\YYYY-MM-DD.md，並在機器人互相討論群貼一行通知。
排程「Gemini研究觀察」每天 09:30 跑；手動：python gemini_watch.py
權限同討論房的 Gemini：只寫 H:\Gemini用，其他只讀，不能執行指令。
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents as rt  # noqa: E402

HOME = rt.GEMINI_HOME
OUT_DIR = os.path.join(HOME, "研究觀察")
SAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "say.py")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, time.strftime("%Y-%m-%d") + ".md")
    if os.path.exists(out) and "--force" not in sys.argv:
        print("今天已經做過：", out)
        return
    prev = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".md"))[-3:]
    prompt = (
        f"你是 Gemini，負責我們研究團隊的「長期研究觀察」。今天是 {time.strftime('%Y-%m-%d')}。\n"
        f"1. 先讀 {os.path.join(HOME, '觀察主題.md')}（主題清單，你可以自己修改它）"
        + (f"，以及前幾天的觀察 {', '.join(os.path.join(OUT_DIR, p) for p in prev)}（避免重複）" if prev else "") + "。\n"
        "2. 依清單挑今天要查的 1–2 個主題（輪流），用網路搜尋找最近的新論文、新工具或規則變動。\n"
        f"3. 把結果寫成 {out}：每一項寫「是什麼、跟我們（螺旋語、手填權重、小網路續學、多 AI 協作）有什麼關係、來源網址」；"
        "只寫查得到來源的，推測要標明；沒有新東西就寫沒有。開頭一行「摘要：…」。\n"
        "4. 最後只回一句話：今天查了哪些主題、最值得注意的一件事。")
    cmd = [rt.AGY_EXE, "-p", prompt, "--model", "gemini-4-hydrogen-high", "--sandbox",
           "--settings", os.path.join(rt.GEMINI_WORK, "agy-settings.json"),
           "--output-format", "json", "--print-timeout", "900s"]
    r = subprocess.run(cmd, cwd=HOME, capture_output=True, timeout=1000, creationflags=rt.NO_WINDOW)
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        data = {"status": "ERROR", "response": r.stderr.decode("utf-8", "replace")[-300:]}
    line = (data.get("response") or "").strip()
    ok = os.path.exists(out)
    msg = (f"🔎 Gemini 今日研究觀察：{line}\n全文：{out}" if ok else
           f"⚠️ Gemini 今日研究觀察沒有寫出檔案（{data.get('status')}）：{line[:300]}")
    tmp = os.path.join(rt.GEMINI_WORK, "watch-msg.txt")
    open(tmp, "w", encoding="utf-8").write(msg)
    subprocess.run(["python", "-B", SAY, "機器人互相討論群", tmp], capture_output=True, timeout=60,
                   creationflags=rt.NO_WINDOW)
    rt.refresh_gemini_usage()
    print(msg)


if __name__ == "__main__":
    main()
