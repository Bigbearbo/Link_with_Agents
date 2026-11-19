r"""摘要：派報員——用免費小模型把大量資訊（群組訊息＋共用區變動）讀過，照每位 AI 的訂閱寫成短摘要，減少大模型要讀的量。
更新：2026-11-19｜作者：Claude（使用者 09:12：「除了條件收訊息，還可以有 AI 派報員，模型公司應該都用小 AI 處理大量資訊，減少大 AI 輸入」）

模型：OpenRouter 免費 nvidia/nemotron-3.5-lightning（輝達那把 $0 鑰匙，算在同一個每日次數裡；每次派報＝每位 1 次呼叫）。
產出：寫進各家 訂閱摘要.md 的「派報員」段（視窗 agent），或給 DeepSeek 的提示用（deepseek_agent 讀 H:\DeepSeek用\派報.md）。
摘要只是導讀，一律標「小模型整理、未核對」，要引用請回原文。
"""
import json, os, sys, time, urllib.request

sys.path.insert(0, r"H:\Agent共用\ai-call\tools\openrouter-worker")
MODEL = "nvidia/nemotron-3.5-lightning:free"
API = "https://openrouter.ai/api/v1/chat/completions"


def _key():
    from credentials import load_key   # 輝達的 $0 鑰匙（worker 的 KEY_FILE）
    return load_key()


def ask_small(prompt, max_tokens=2500):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}  # 免費模型的供應商會留資料，限定 deny 會找不到端點（404）；所以派報內容只放群組訊息和檔名摘要，不放秘密
    r = urllib.request.Request(API, data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + _key(), "Content-Type": "application/json", "X-Title": "roundtable"})
    d = json.load(urllib.request.urlopen(r, timeout=120))
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "").strip()   # 會先「思考」的模型，token 太少時 content 是空的


def brief_for(name, interests, messages, changes):
    """messages＝[(誰, 時間, 文字)]；changes＝訂閱篩過的變動條列。回傳 10 行內的摘要。"""
    msg = "\n".join(f"【{w}｜{t}】{x[:400]}" for w, t, x in messages[-40:])
    prompt = (f"你是派報員。讀者是團隊成員「{name}」，他關心：{'、'.join(interests) or '全部'}。\n"
              "下面是最近的群組訊息和共用區檔案變動。請用繁體中文，寫 10 行以內的重點：只挑跟他有關、他需要知道或要行動的，"
              "每行開頭寫來源（誰幾點或檔名）。沒有相關的就寫「沒有跟你相關的新事」。不要加入原文沒有的內容。\n\n"
              f"## 群組訊息\n{msg or '（無）'}\n\n## 檔案變動\n{changes or '（無）'}")
    return ask_small(prompt)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    print(ask_small("用一句繁體中文說你是誰。"))
