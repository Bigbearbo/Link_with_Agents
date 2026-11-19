r"""網頁巡檢員：用沒有視窗的 Edge 把聊天網頁截圖（電腦寬、手機寬），交給便宜的看圖模型找版面問題，寫報告。
使用者 11-20 00:50：「得有個 AI 能像我一樣回報網頁使用資訊，這樣就不用我一直當測試員，一個眼睛不錯的 AI？操作頁面耗能低又能找出問題的。」

隱私：主畫面只拍「小模型工作群」空房（網址帶 ?room=small-pool），不拍主討論私人內容；另外拍研究、待討論、變動三頁。
看圖模型：Claude Haiku 5.5（Claude API 贈點，每張約 0.1 美分），一定 paid=True。
  python 網頁巡檢.py           截圖＋看圖，報告寫 logs\網頁巡檢\報告-<時間>.md
  python 網頁巡檢.py 只截圖     不呼叫模型（0 成本）
"""
import base64, json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = os.path.join(HERE, "logs", "網頁巡檢")
PAGES = [("主畫面（空房）", "/?room=small-pool"), ("研究", "/research"), ("待討論", "/decisions"), ("變動", "/changes")]
SIZES = [("電腦", "1400,900"), ("手機", "390,844")]
CHECK = ("你是網頁測試員，看這張聊天程式的截圖（{name}，{size}寬），用繁體中文列出看得到的問題，每點一行、最多 6 點："
         "文字被切掉或重疊、按鈕擠在一起或超出畫面、看不清楚（顏色對比太低）、版面歪掉、空白太多、有錯誤訊息。"
         "沒有問題就只回「看起來正常」。不要猜看不到的功能。")


def shoot(url, path, size):
    k = open(os.path.join(HERE, "secrets", "web-key.txt"), encoding="utf-8").read().strip()
    sep = "&" if "?" in url else "?"
    full = f"http://127.0.0.1:8765{url}{sep}k={k}"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={size}",
                    "--virtual-time-budget=6000", f"--screenshot={path}", full],
                   capture_output=True, timeout=90, creationflags=0x08000000)
    return os.path.exists(path)


def look(path, name, size):
    sys.path.insert(0, r"H:\Agent共用\ai-call")
    import importlib
    pool = importlib.import_module("免費AI池")
    img = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": "claude-haiku-5-5", "max_tokens": 2000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}},
                {"type": "text", "text": CHECK.format(name=name, size=size)}]}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": pool.load("anthropic"), "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    u = d.get("usage", {})
    usd = (u.get("input_tokens", 0) * 0.10 + u.get("output_tokens", 0) * 0.50) / 1e6
    with open(os.path.join(r"H:\Agent共用\ai-call", "免費AI池-用量.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "服務": "anthropic", "模型": "claude-haiku-5-5",
                            "tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0), "in": u.get("input_tokens"),
                            "out": u.get("output_tokens"), "usd": round(usd, 6), "註": "網頁巡檢"}, ensure_ascii=False) + "\n")
    return "\n".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text").strip(), usd


def main(no_model=False):
    stamp = time.strftime("%m%d-%H%M")
    os.makedirs(OUT, exist_ok=True)
    total = 0.0
    lines = [f"# 網頁巡檢報告 {time.strftime('%Y-%m-%d %H:%M')}", "看圖模型：" + ("（只截圖）" if no_model else "Claude Haiku 5.5"), ""]
    for name, url in PAGES:
        for sz, dim in SIZES:
            path = os.path.join(OUT, f"{stamp}-{name}-{sz}.png".replace("（", "").replace("）", ""))
            if not shoot(url, path, dim):
                lines.append(f"## {name}／{sz}\n截圖失敗\n")
                continue
            if no_model:
                lines.append(f"## {name}／{sz}\n截圖：{path}\n")
                continue
            try:
                text, usd = look(path, name, sz)
                total += usd
            except Exception as e:
                text = f"看圖失敗：{type(e).__name__} {e}"
            lines.append(f"## {name}／{sz}\n{text}\n截圖：{path}\n")
    lines.append(f"\n合計約 ${total:.4f}")
    rep = os.path.join(OUT, f"報告-{stamp}.md")
    open(rep, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("報告：", rep)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    main(no_model=sys.argv[1:2] == ["只截圖"])
