r"""每天一次：輝達在用的 OpenRouter 免費模型還是不是免費（使用者 11-17 20:34「你可以去OPENROUTER查規則，每天查一次好了，以免哪天沒免費了」）。

原本寫在 nvidia_agent.py 的提示裡、靠輝達自己記得查；11-17 21:05 查了一次（年份還寫成 2025），11-18 輝達搬進 OpenCode 視窗後那段提示沒在用，之後沒再查過。
改成程式查：讀 opencode.json 的 model／small_model（只讀），比對 OpenRouter 公開目錄（不用金鑰），價格全 0 才算免費。
  python 免費規則檢查.py        → 立刻查一次，印結果
  hub 每小時呼叫 daily(hub)：今天查過就跳過；有模型不見或變收費才在互相討論群 @Claude。
紀錄：logs\免費規則檢查.jsonl（一天一行）。只查模型價格；每日次數上限（50／1000）不在公開目錄裡，不在這裡查。
"""
import json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "免費規則檢查.jsonl")
OPENCODE = r"H:\免費模型用\opencode\opencode.json"


def used_models():
    d = json.load(open(OPENCODE, encoding="utf-8"))
    out = []
    for k in ("model", "small_model"):
        v = str(d.get(k) or "")
        if v.startswith("openrouter/"):
            out.append(v[len("openrouter/"):])
    return out


def check():
    used = used_models()
    req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "hub-free-check"})
    data = json.load(urllib.request.urlopen(req, timeout=30))["data"]
    cat = {m["id"]: m for m in data}
    bad = []
    for mid in used:
        m = cat.get(mid)
        if not m:
            bad.append(f"{mid} 不在目錄裡了")
        elif not all(float(v or 0) == 0 for v in (m.get("pricing") or {}).values()):
            bad.append(f"{mid} 價格不是 0：{m.get('pricing')}")
    n_free = sum(1 for i, m in cat.items() if i.endswith(":free"))
    return {"日期": time.strftime("%Y-%m-%d"), "時間": time.strftime("%H:%M"), "用的模型": used,
            "目錄裡 :free 數": n_free, "問題": bad}


def last_day():
    try:
        return json.loads(open(LOG, encoding="utf-8").read().strip().splitlines()[-1]).get("日期")
    except Exception:
        return None


def daily(hub):
    if last_day() == time.strftime("%Y-%m-%d"):
        return
    r = check()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if r["問題"]:
        room = hub.rooms.get("g1004308131170")
        if room:
            room.notice("⚠️ OpenRouter 免費檢查：" + "；".join(r["問題"]) +
                        "\n輝達可能開始花錢或叫不動。@Claude 請確認並告訴使用者（換模型要改 opencode.json，屬使用者設定）。")


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
