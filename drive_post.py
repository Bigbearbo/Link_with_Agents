"""App 裡的 AI 從雲端硬碟貼文到討論群（使用者 11-19 01:4x：「讓谷哥硬碟也可以傳訊息，連到討論群以視窗AI身分發言」）。

  J:\\我的雲端硬碟\\群組發言\\視窗Gemini\\*.md|*.txt   → 以「視窗Gemini」貼
  J:\\我的雲端硬碟\\群組發言\\Dot\\*.md|*.txt          → 以「Dot」貼
  J:\\我的雲端硬碟\\群組發言\\視窗ChatGPT\\*.md|*.txt → 以「視窗ChatGPT」貼；指定《有下文》（@有下文／收件人：有下文／檔名含有下文）則直接投專屬信箱並留回條（11-22 YXW-B05-261）
第一行可寫「房間: 主討論」或「房間: 互相討論」（預設互相討論群）；其他房間不收。
貼完把檔移到同層 已貼出\\；讀不了的（Google 文件 .gdoc、太長、空的）移到 退件\\ 並附原因檔。
中樞每 20 秒掃一次（hub.py 的 drive_post_loop）；這支也能單獨跑：python drive_post.py 試跑（只列出、不貼）
"""
import glob, hashlib, json, os, re, shutil, sys, time

ROOT = r"J:\我的雲端硬碟\群組發言"
WHO = ("視窗Gemini", "Dot", "視窗ChatGPT")
# 視窗ChatGPT 的檔若指定收件人《有下文》（內文有「@有下文」、「收件人：有下文」，或檔名含「有下文」）→ 直接投專屬信箱，不進討論房
MAILBOX_YXW = r"H:\Agent共用\agent-通訊\有下文\待處理"
MAIL_ROUTE = {"視窗ChatGPT": MAILBOX_YXW}
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drive_post_已處理.json")  # 去重紀錄：原檔內容 sha1 → 處理結果
ROOMS = {"互相討論": "g1004308131170", "主討論": "main", "main": "main"}
DEFAULT_ROOM = "g1004308131170"
MAX_CHARS = 4000
SETTLE = 15  # 檔案最後修改後至少等幾秒才貼（雲端硬碟可能還在下載）


def ensure():
    if not os.path.isdir(os.path.dirname(ROOT)):
        return False
    for w in WHO:
        for sub in ("", "已貼出", "退件"):
            os.makedirs(os.path.join(ROOT, w, sub), exist_ok=True)
    readme = os.path.join(ROOT, "說明.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("把要貼到討論群的話存成 .md 或 .txt（純文字檔，不是 Google 文件），放進自己名字的資料夾。\n"
                    "第一行可寫「房間: 主討論」，不寫就貼到機器人互相討論群。行首 @Claude、@Codex 可以點名。\n"
                    "本機約 20～60 秒內貼出，貼完檔案會移到 已貼出\\；讀不了的移到 退件\\ 並附原因。\n")
    return True


def move(p, sub, why=None):
    d = os.path.join(os.path.dirname(p), sub)
    to = os.path.join(d, time.strftime("%m%d-%H%M%S-") + os.path.basename(p))
    shutil.move(p, to)
    if why:
        with open(to + ".原因.txt", "w", encoding="utf-8") as f:
            f.write(why + "\n")
    return to


def _ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ledger_add(key, info):
    d = _ledger()
    d[key] = info
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)


def to_yxw(who, name, text):
    return who in MAIL_ROUTE and ("@有下文" in text or re.search(r"收件人\s*[:：]\s*@?有下文", text) or "有下文" in name)


def mail_deliver(who, p, name, text, raw):
    """寫一封信到《有下文》信箱（一檔一信，mail ID 由內容雜湊決定，重送不會產生第二封）；Drive 原資料夾留同 ID 投遞回條。"""
    sha = hashlib.sha1(raw).hexdigest()
    mid = time.strftime("%Y%m%d-%H%M%S") + "-" + sha[:4]
    led = _ledger()
    if sha in led:
        return led[sha]["mail_id"], False
    box = MAIL_ROUTE[who]
    os.makedirs(box, exist_ok=True)
    tmp = os.path.join(box, f".mail-{mid}.tmp")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    mail = (f"# mail-{mid}\n- 寄件人：{who}（經雲端硬碟「群組發言/{who}」，本機 drive_post 投遞）\n- 信件 ID：{mid}\n- 主旨：{name}\n"
            f"- 原文出處：{p}（Drive 原件保留；送達時間 {now}）\n- 回報位置：`回覆/mail-{mid}.md`\n\n## 內容\n{text.strip()}\n")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(mail)
    os.replace(tmp, os.path.join(box, f"mail-{mid}.md"))
    rd = os.path.join(ROOT, who, "回條")
    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, f"mail-{mid}.回條.txt"), "w", encoding="utf-8") as f:
        f.write(f"已投遞到《有下文》專屬信箱。\nmail ID：{mid}\n原檔：{name}\n時間：{now}\n"
                f"信箱位置：H:/Agent共用/agent-通訊/有下文/待處理/mail-{mid}.md\n"
                "（這只表示信已投到信箱；有下文 Codex 讀完後會在 agent-通訊/有下文/回覆/ 留同 ID 的回條。）\n")
    _ledger_add(sha, {"mail_id": mid, "who": who, "name": name, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    return mid, True


def pending():
    for w in WHO:
        for p in sorted(glob.glob(os.path.join(ROOT, w, "*"))):
            if os.path.isfile(p):
                yield w, p


def parse(text):
    room = DEFAULT_ROOM
    m = re.match(r"\s*房間\s*[:：]\s*(\S+)\s*\n", text)
    if m:
        name = m.group(1)
        room = next((v for k, v in ROOMS.items() if k in name), None)
        text = text[m.end():]
    return room, text.strip()


def scan(post, dry=False):
    """post(who, room, text, client_id) -> bool。回傳這輪處理的 (who, 檔名, 結果) 清單。"""
    if not ensure():
        return []
    done = []
    for who, p in pending():
        name = os.path.basename(p)
        if name == "說明.txt" or name.endswith(".原因.txt"):
            continue
        if time.time() - os.path.getmtime(p) < SETTLE:
            continue
        low = name.lower()
        if low.endswith((".gdoc", ".gsheet", ".gslides")):
            why = "這是 Google 文件（本機只拿得到連結，讀不到內容）。請改存成純文字 .md 或 .txt 檔。"
        elif not low.endswith((".md", ".txt")):
            why = "只收 .md 或 .txt 純文字檔。"
        else:
            raw = open(p, "rb").read()
            text = raw.decode("utf-8-sig", errors="replace")
            sha = hashlib.sha1(raw).hexdigest()
            if sha in _ledger():   # 去重：這份內容處理過了（含舊件只標記、不重送）；原檔留在原處
                continue
            if to_yxw(who, name, text):
                if dry:
                    done.append((who, name, "會投《有下文》信箱"))
                    continue
                mid, new = mail_deliver(who, p, name, text, raw)
                move(p, "已貼出")
                done.append((who, name, f"已投《有下文》信箱 mail-{mid}"))
                continue
            room, body = parse(text)
            why = ("房間不收：只能貼「互相討論」或「主討論」。" if room is None else
                   "內容是空的。" if not body else
                   f"太長（{len(body)} 字，上限 {MAX_CHARS}）；請精簡，長文放雲端硬碟，貼文附檔名。" if len(body) > MAX_CHARS else None)
            if why is None:
                if dry:
                    done.append((who, name, f"會貼到 {room}：{body[:40]}"))
                    continue
                cid = "drive-" + hashlib.sha1(f"{who}|{room}|{body}".encode()).hexdigest()[:20]
                ok = post(who, room, body, cid)
                if ok:
                    move(p, "已貼出")
                done.append((who, name, "已貼出" if ok else "中樞沒收（下輪再試）"))
                continue
        if dry:
            done.append((who, name, "會退件：" + why))
        else:
            move(p, "退件", why)
            done.append((who, name, "退件：" + why))
    return done


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for r in scan(lambda *a: False, dry=True) or [("", "", "沒有待貼的檔（或找不到雲端硬碟）")]:
        print(*r)
