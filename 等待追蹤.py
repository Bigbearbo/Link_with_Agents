r"""誰在等誰（同步點）追蹤。使用者 11-20 06:28：「感覺是同步處理機置，但大家都忘了，只剩下輝達記得?」
——輝達 11-18 在自己的目標檔寫「請 Claude 核對」就停下來等，這句只存在它的目標檔，沒有進待辦、沒人提醒，所以被等的人忘了。

做法：讀各 AI 目標檔「## 下一步」那段，找「請／等／待 ＋ 名字」或「@名字」，當成一個等待點；
  waits()            → [{等的人, 被等的人, 內容, 檔案, 等了幾小時}]
  remind(hub)        → 等超過 REMIND_H 小時、而且 12 小時內沒提醒過的，在互相討論群 @被等的人（每小時由 hub 呼叫）
  python 等待追蹤.py  → 印出目前的等待點
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS = {"輝達": r"H:\免費模型用\目標.md", "Gemini": r"H:\Gemini用\目標.md", "DeepSeek": r"H:\DeepSeek用\目標.md"}
NAMES = ["Claude", "Codex", "Gemini", "輝達", "DeepSeek", "使用者"]
STATE = os.path.join(HERE, "logs", "等待提醒.json")
REMIND_H = 6


def next_section(text):
    m = re.search(r"^##\s*下一步\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def waits():
    out = []
    for who, p in GOALS.items():
        try:
            t = open(p, encoding="utf-8").read()
        except OSError:
            continue
        age = (time.time() - os.path.getmtime(p)) / 3600
        for line in next_section(t).splitlines():
            line = line.strip(" -*0123456789.")
            for n in NAMES:
                if n != who and re.search(rf"(請|等|待|需要|@)\s*{n}|{n}\s*(回覆|核對|審|確認|驗)", line):
                    out.append({"等的人": who, "被等的人": n, "內容": line[:160], "檔案": p, "等了幾小時": round(age, 1)})
                    break
    return out


def remind(hub):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    room = hub.rooms.get("g1004308131170")
    if not room:
        return
    for w in waits():
        k = f"{w['等的人']}>{w['被等的人']}|{w['內容'][:40]}"
        if w["等了幾小時"] < REMIND_H or time.time() - st.get(k, 0) < 12 * 3600 or w["被等的人"] == "使用者":
            continue
        st[k] = time.time()
        room.notice(f"⏳ 同步點：{w['等的人']} 在等 @{w['被等的人']}（已 {w['等了幾小時']:.0f} 小時）：「{w['內容']}」\n"
                    f"出處 {w['檔案']}。做完請在群裡回 {w['等的人']}；不做也請說一聲，讓它改做別的。")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    for w in waits():
        print(w)
