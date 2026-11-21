r"""資料夾入口（使用者 11-21 02:35：「"本房"那邊幫我把你們資料夾的各種入口設計一下連結，介紹一下裡面有什麼」）。

一份清單 ENTRIES，兩種輸出：
- section_html(esc) → 網頁「設定」頁最上面一段（按「⚙️ 本房」就會打開）；每列有「📂 開啟」按鈕（POST /api/open，只開清單裡的路徑）。
- ai_text() → H:\Agent共用\資料夾入口.md（給 AI 讀）。
每列自動補：裡面有幾項、最近一次變動是哪個檔、何時。說明文字改這裡。
"""
import os
import time

AI_OUT = r"H:\Agent共用\資料夾入口.md"

# (分組, 名稱, 路徑, 誰讀寫, 裡面有什麼)
ENTRIES = [
    ("各 AI 的家", "Claude", r"H:\Claude用", "Claude 寫；使用者",
     "中樞程式（三方討論\\）、輸入法詞庫、手動備份清單、Chrome 分頁備份"),
    ("各 AI 的家", "Codex", r"H:\Codex用", "Codex 寫；其他人唯讀",
     "螺旋語研究正本 Rasengan_Binary_Creator、課程、實驗輸出"),
    ("各 AI 的家", "螺旋語研究（Rasengan）", r"H:\Codex用\Rasengan_Binary_Creator", "Codex 寫；其他人唯讀",
     "螺旋語原始設計、README 技術總綱、倉鼠設計；查原設計先來這"),
    ("各 AI 的家", "Gemini", r"H:\Gemini用", "Gemini 寫",
     "Gemini 的目標、筆記、研究觀察、手搓、聊天程式修改提案"),
    ("各 AI 的家", "輝達（OpenCode）", r"H:\免費模型用", "輝達寫",
     "輝達的目標、筆記、工作區 work\\、OpenCode 設定（opencode\\）"),
    ("各 AI 的家", "DeepSeek", r"H:\DeepSeek用", "DeepSeek 寫",
     "AI 評比題庫、自主規格、目標、筆記"),
    ("共用區", "共用區首頁", r"H:\Agent共用", "所有 AI；各寫自己的子資料夾",
     "大家交換資料的地方；README、團隊目標、新成員入門、用量狀態、中樞參數"),
    ("共用區", "手搓算術", r"H:\Agent共用\專案\手搓算術", "手搓匠、Claude",
     "手搓佇列、各段零件、總驗收、Verilog 實驗、教學線"),
    ("共用區", "課程", r"H:\Agent共用\課程", "Claude、Codex",
     "課程表.json（100 題、0–13 段）"),
    ("共用區", "專案", r"H:\Agent共用\專案", "各專案負責人",
     "手搓算術、團隊系統設計、外部研究等"),
    ("共用區", "研究想法庫", r"H:\Agent共用\研究想法庫", "所有 AI",
     "還沒做的研究點子，一個檔一題（例：倉鼠世界、保證區鎖）"),
    ("共用區", "知識庫", r"H:\Agent共用\知識庫", "Codex 維護",
     "名詞、設計、規則；網頁「📖 知識庫」就是讀這裡"),
    ("共用區", "待辦", r"H:\Agent共用\待辦", "所有 AI",
     "待辦.json：誰手上有什麼還沒完成"),
    ("共用區", "對話紀錄匯出", r"H:\Agent共用\對話紀錄匯出", "匯出程式寫；所有 AI 讀",
     "過了隱私篩的對話紀錄，給外部模型的 AI 讀；註解另存 註解\\"),
    ("共用區", "使用者的 GPT 對話紀錄", r"H:\Agent共用\使用者的GPT對話紀錄_2026_11", "只讀",
     "使用者匯出的 GPT 歷史對話（螺旋語初期討論在這）"),
    ("共用區", "權限", r"H:\Agent共用\權限", "Claude",
     "權限提案、審查程式提案；總表在 權限總覽.md"),
    ("App 裡的 AI", "視窗 AI（App）用", r"H:\Agent共用\視窗AI(APP)用", "GPT App、Dot、視窗 Gemini 交件",
     "App 裡 AI 的使用說明、交件區"),
    ("App 裡的 AI", "工單 inbox", r"H:\Agent共用\inbox", "App 的 AI 寫；本機處理",
     "Dot、其他 App 的工單；處理完移到 done\\"),
    ("App 裡的 AI", "雲端：群組發言", r"J:\我的雲端硬碟\群組發言", "App 的 AI 寫",
     "放 .md/.txt，中樞每 20 秒貼進討論房（以它的名字發言）"),
    ("App 裡的 AI", "雲端：工單收件", r"J:\我的雲端硬碟\工單收件", "App 的 AI 寫",
     "放 <名>.req.txt，本機每 15 秒認領、自動回 .answer.md"),
    ("中樞與紀錄", "中樞程式", r"H:\Claude用\三方討論", "Claude；Codex 讀",
     "hub.py 等中樞程式、聊天原始紀錄；外部模型的 AI 不讀這裡"),
    ("中樞與紀錄", "對話紀錄（按日）", r"H:\Claude用\三方討論\logs-按日", "中樞寫",
     "每個房間一天一份 .md（未過隱私篩的原始版）"),
    ("中樞與紀錄", "時間戳收據", r"H:\Agent共用\時間戳", "Claude",
     "研究成果的時間戳證明"),
    ("備份", "雲端備份", r"J:\我的雲端硬碟\研究備份", "雲端備份.py 每小時",
     "研究倉庫打包、聊天日誌、時間戳；不含鑰匙"),
    ("備份", "鑰匙", r"H:\金鑰", "只有中樞程式讀；使用者存",
     "網頁鑰匙、AI 發言鑰匙、各家 API key（11-21 從中樞搬來）"),
    ("備份", "鑰匙備份（E 槽）", r"E:\金鑰備份", "手動",
     "鑰匙的普通備份＋手動備份清單.md；不同步雲端"),
]


def paths():
    return {os.path.normcase(p) for _, _, p, _, _ in ENTRIES}


def _stat(p):
    """回 (項數, 最近變動檔名, 時間字串)；不在就回 None。只看第一層，快。"""
    try:
        names = [n for n in os.listdir(p) if not n.startswith((".", "__pycache__", "desktop.ini"))]
    except OSError:
        return None
    best, bt = "", 0
    for n in names:
        try:
            t = os.path.getmtime(os.path.join(p, n))
        except OSError:
            continue
        if t > bt:
            best, bt = n, t
    return len(names), best, time.strftime("%m-%d %H:%M", time.localtime(bt)) if bt else "—"


def section_html(esc):
    rows, cur = [], None
    for g, name, p, who, what in ENTRIES:
        if g != cur:
            rows.append(f"<tr><td colspan='4' style='padding-top:10px'><b>{esc(g)}</b></td></tr>")
            cur = g
        s = _stat(p)
        info = (f"{s[0]} 項；最近：{esc(s[1][:40])}（{s[2]}）" if s else "⚠️ 目前找不到（雲端硬碟沒開？）")
        rows.append(f"<tr><td><b>{esc(name)}</b><br><span class='meta'>{esc(p)}</span></td>"
                    f"<td>{esc(what)}<br><span class='meta'>誰讀寫：{esc(who)}｜{info}</span></td>"
                    f"<td><button onclick=\"openDir({esc(repr(p))},this)\">📂 開啟</button></td></tr>")
    return ("<section id='folders'><h3>📂 資料夾入口</h3><p class='meta'>按「開啟」會在這台電腦的檔案總管打開。"
            "給 AI 讀的版本＝H:\\Agent共用\\資料夾入口.md。說明要改，跟 Claude 說（清單在 資料夾入口.py）。</p>"
            "<table>" + "".join(rows) + "</table></section>")


OPEN_JS = """async function openDir(p,b){const t=b.textContent;b.textContent='開啟中…';try{const r=await fetch('/api/open',{method:'POST',headers:{'X-Key':K,'Content-Type':'application/json'},body:JSON.stringify({path:p})});const j=await r.json();b.textContent=j.ok?'✓ 已開':'失敗';}catch(e){b.textContent='失敗';}setTimeout(()=>b.textContent=t,2000);}"""


def open_dir(p):
    """只開清單裡的路徑（網頁傳任意路徑也不會被拿去開別的）。"""
    if os.path.normcase(str(p or "")) not in paths() or not os.path.isdir(p):
        return False
    os.startfile(p)
    return True


def ai_text():
    L = [f"# 資料夾入口（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝設定頁最上面「📂 資料夾入口」）", ""]
    cur = None
    for g, name, p, who, what in ENTRIES:
        if g != cur:
            L += ["", f"## {g}", "", "| 名稱 | 路徑 | 裡面有什麼 | 誰讀寫 | 最近變動 |", "|---|---|---|---|---|"]
            cur = g
        s = _stat(p)
        L.append(f"| {name} | {p} | {what} | {who} | " + (f"{s[0]} 項；{s[1]}（{s[2]}）" if s else "找不到") + " |")
    L += ["", "規則：外部模型的 AI（輝達、免費池）只讀共用區與對話紀錄匯出，不讀中樞程式與鑰匙；各寫自己的家。"]
    return "\n".join(L) + "\n"


def write():
    open(AI_OUT, "w", encoding="utf-8", newline="\n").write(ai_text())
    try:   # 使用者 11-21 07:27：資源資訊改成推播給 AI（借這個每 10 分鐘的掛點，不另改 hub.py）
        import importlib
        importlib.import_module("資源推播").tick()
    except Exception:
        pass
    try:   # 使用者 11-21 08:57：不在電腦前時，輝達權限等 30 分鐘就自動拒絕並請它改找隊友（只拒絕、不代按允許）
        import importlib
        importlib.import_module("權限逾時").tick()
    except Exception:
        pass
    try:   # 使用者 11-21 11:49：完整驗收不急 → 排閒時佇列，使用者不在電腦前才開下一件
        import importlib.util
        s = importlib.util.spec_from_file_location("閒時驗收", r"H:\Agent共用\專案\團隊系統\閒時驗收.py")
        m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
        m.tick(importlib.import_module("權限逾時").user_away())
    except Exception:
        pass
    try:   # 使用者 11-21 08:06：派工建議（第 2、3 站）跟著每 10 分鐘刷新
        import runpy
        runpy.run_path(r"H:\Agent共用\待辦\派工建議.py", run_name="__main__")
    except BaseException:
        pass


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    write()
    print(ai_text())
