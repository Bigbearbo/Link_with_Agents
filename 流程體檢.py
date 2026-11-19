r"""每日流程體檢（使用者 11-20 07:05「需要一個專門思考如何優化你們的工作流程子代理嗎」→ Claude 建議：不要常駐空想的代理，
改成每天一次、程式先數、AI 只看數字挑最多 3 件最該修的）。

  python 流程體檢.py [日期 YYYY-MM-DD]   → 寫 logs\流程體檢\<日期>.md，印出摘要
  hub 每小時迴圈呼叫 daily(hub)：每天 07 點後跑一次，結果貼到互相討論群（不吵主討論）。
指標（全部從現有紀錄算，不用 AI）：
  備援接手次數、輝達套句被拿掉次數、同步點逾時、誤會更正新增、求救卡／報錯、叫醒失敗、小 AI 發言數
"""
import glob, importlib, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "流程體檢")


def day_logs(day):
    txt = {}
    for p in glob.glob(os.path.join(HERE, "logs-按日", "*", f"{day}.md")):
        txt[os.path.basename(os.path.dirname(p))] = open(p, encoding="utf-8", errors="replace").read()
    return txt


def metrics(day):
    md = day[5:]   # MM-DD
    logs = day_logs(day)
    allt = "\n".join(logs.values())
    m = {}
    m["備援 Claude 接手"] = allt.count("改由備援 Claude 回答")
    m["輝達套句被拿掉"] = allt.count("拿掉一句沒附檔名")
    m["小 AI 發言"] = len(re.findall(r"^## 小AI·", allt, re.M))
    m["使用者發言"] = len(re.findall(r"^## 使用者", allt, re.M))
    try:
        sys.path.insert(0, HERE)
        w = importlib.import_module("等待追蹤").waits()
        m["同步點逾時(>6h)"] = sum(1 for x in w if x["等了幾小時"] > 6 and x["被等的人"] != "使用者")
    except Exception:
        m["同步點逾時(>6h)"] = "讀不到"
    try:
        fix = open(r"H:\Agent共用\誤會更正.md", encoding="utf-8").read()
        m["誤會更正新增"] = len(re.findall(rf"^## .*{md}", fix, re.M))
    except OSError:
        m["誤會更正新增"] = "讀不到"
    try:
        cards = json.load(open(os.path.join(HERE, "logs", "求救卡.json"), encoding="utf-8"))
        today = [c for c in cards.values() if str(c.get("建立", "")).startswith(md)]
        m["求救卡（今天開／未解）"] = f"{len(today)}／{sum(1 for c in cards.values() if c.get('狀態') != '已解決')}"
    except (OSError, ValueError):
        m["求救卡（今天開／未解）"] = "0／0"
    try:
        m["報錯"] = sum(1 for l in open(os.path.join(HERE, "logs", "報錯.jsonl"), encoding="utf-8") if md in l or day in l)
    except OSError:
        m["報錯"] = 0
    try:
        wake = [l for l in open(os.path.join(HERE, "logs", "叫醒.log"), encoding="utf-8") if l.startswith(md)]
        m["叫醒失敗"] = sum(1 for l in wake if "叫不醒" in l or "不再自動叫" in l)   # Gemini「沒有開視窗或不支援，只留單」是設計如此，不算
    except OSError:
        m["叫醒失敗"] = 0
    return m, logs


def ai_pick(m, day):
    """把數字（不含對話原文）交給免費小 AI 挑最多 3 件；失敗就只留數字。"""
    try:
        sys.path.insert(0, r"H:\Agent共用\ai-call")
        pool = importlib.import_module("免費AI池")
        prompt = ("下面是一個多 AI 協作聊天系統某一天的流程指標。請用繁體中文挑出最多 3 件最該修的問題，"
                  "每件一行：「問題｜依據哪個數字｜建議的最小修法」。數字是 0 的不要提；沒有明顯問題就回「今天沒有要修的」。\n"
                  + json.dumps(m, ensure_ascii=False))
        return pool.ask("groq", prompt, max_tokens=1200, timeout=60).strip()
    except Exception as e:
        return f"（AI 挑選沒跑成：{type(e).__name__}）"


def run(day=None):
    day = day or time.strftime("%Y-%m-%d")
    m, _ = metrics(day)
    pick = ai_pick(m, day)
    os.makedirs(OUT, exist_ok=True)
    lines = [f"# 流程體檢 {day}", "", "| 指標 | 數值 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m.items()] + \
            ["", "## 小 AI 挑的（只看上面數字，僅供參考）", pick, "",
             "> 能直接修的小事由 Claude 修並記 AI自決紀錄；要使用者決定的才進待討論。連續多天為 0 的指標之後拿掉。"]
    p = os.path.join(OUT, f"{day}.md")
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    return p, m, pick


def daily(hub):
    """hub 每小時呼叫：07 點後、今天還沒跑過就跑一次，摘要貼互相討論群。"""
    day = time.strftime("%Y-%m-%d")
    if time.localtime().tm_hour < 7 or os.path.exists(os.path.join(OUT, f"{day}.md")):
        return
    p, m, pick = run(day)
    room = hub.rooms.get("g1004308131170")
    if room:
        nz = "、".join(f"{k} {v}" for k, v in m.items() if v not in (0, "0／0"))
        room.notice(f"🩺 今日流程體檢：{nz or '各項都是 0'}\n{pick[:600]}\n全文 {p}。\n"
                    "@Claude 請叫「流程優化員」子代理（.claude\\agents\\flow-optimizer.md）處理：權限內可還原的直接修＋記自決紀錄，"
                    "跟使用者／權限有關的寫成提案（使用者 11-20 07:07）。")
        try:   # 系統通知不會自己叫醒人：直接請終端機 Claude 接這一輪
            room.stop_flag = False
            room.chain([room.claude])
        except Exception:
            pass


if __name__ == "__main__":
    p, m, pick = run(sys.argv[1] if len(sys.argv) > 1 else None)
    print(p)
    print(json.dumps(m, ensure_ascii=False, indent=1))
    print(pick)
