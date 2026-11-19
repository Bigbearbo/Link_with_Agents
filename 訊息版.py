r"""每位 AI 的訊息版（使用者 11-20 04:31／04:33：把 Bot 當成人、有自己的社群訊息版；參考主流社群媒體）
Codex 04:34 參考 Slack／Discord／LINE／X 整理的三個入口：
  1. 等我處理（像 Slack 活動頁、Discord 點名）：被 @ 之後自己還沒在那個房間回話的；自己負責、沒完成的待辦。讀過但沒做完就繼續留著。
  2. 我追蹤的（像 X「正在跟隨」）：派報生替他整理的「下一期」新聞、最近叫醒紀錄，附「為什麼跟你有關」。
  3. 全部動態（像 Slack 討論串）：機器人群最近的發言，同一人連續的收在一起。
board(hub, who) → dict；page(hub, who, key) → HTML（右側面板用）。
"""
import html as _h, json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = ["Claude", "Codex", "Gemini", "輝達", "DeepSeek", "有下文"]


def waiting(hub, who, hours=24):
    """被 @ 之後，自己在同一房間還沒回話的訊息。"""
    out = []
    cut = time.strftime("%m-%d %H:%M", time.localtime(time.time() - hours * 3600))
    for rid, room in hub.rooms.items():
        t = room.transcript
        for i, m in enumerate(t):
            if m.get("time", "") < cut or m.get("who") == who or f"@{who}" not in (m.get("text") or ""):
                continue
            if any(n.get("who") == who for n in t[i + 1:]):
                continue
            out.append({"房間": room.name, "時間": m.get("time"), "誰": m.get("who"), "內容": (m.get("text") or "").strip()[:240]})
    return out[-15:][::-1]


def my_todos(who):
    try:
        todos = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for t in todos:
        if t.get("狀態") == "完成" or who not in str(t.get("負責", "")):
            continue
        recs = [str(r.get("內容", r) if isinstance(r, dict) else r) for r in (t.get("紀錄") or [])]
        out.append({"id": t["id"], "標題": str(t.get("標題", ""))[:60], "狀態": t.get("狀態", ""), "最後": recs[-1][:120] if recs else "（還沒紀錄）"})
    return out


def rescues(who):
    """還沒解決、而且負責人或發起人是他的求救卡（求救.py）。"""
    try:
        cards = json.load(open(os.path.join(HERE, "logs", "求救卡.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"id": k, **v} for k, v in cards.items() if v.get("狀態") != "已解決" and who in (v.get("負責", ""), v.get("發起", ""))]


def following(who):
    news = ""
    p = os.path.join(HERE, "logs", "派報", f"{who}-下一期.md")
    if os.path.exists(p):
        news = open(p, encoding="utf-8").read()[-3000:]
    wakes = []
    try:
        for line in open(os.path.join(HERE, "logs", "派報生.jsonl"), encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("給") == who:
                wakes.append(r)
    except OSError:
        pass
    return news, wakes[-8:][::-1]


def feed(hub, n=30):
    room = hub.rooms.get("g1004308131170")
    if not room:
        return []
    out = []
    for m in room.transcript[-n * 2:]:
        if m.get("who") == "系統":
            continue
        if out and out[-1]["誰"] == m.get("who"):
            out[-1]["內容"] += "\n" + (m.get("text") or "")[:200]
            continue
        out.append({"誰": m.get("who"), "時間": m.get("time"), "內容": (m.get("text") or "")[:300]})
    return out[-n:][::-1]


def page(hub, who, key):
    who = who if who in NAMES else "Claude"
    w, todos, sos = waiting(hub, who), my_todos(who), rescues(who)
    try:   # 使用者 11-20 06:28：同步點（別人目標檔裡寫「請／等 你」）也算「等你處理」，不受 24 小時限制
        import importlib
        w = [{"房間": "目標檔", "時間": f"{x['等了幾小時']:.0f} 小時前", "誰": x["等的人"], "內容": "⏳ 在等你：" + x["內容"]}
             for x in importlib.import_module("等待追蹤").waits() if x["被等的人"] == who] + w
    except Exception:
        pass
    news, wakes = following(who)
    esc = _h.escape
    tabs = "".join(f'<a class="{"sel" if n == who else ""}" href="/board?ai={esc(n)}&k={esc(key)}">{esc(n)}</a>' for n in NAMES)
    wl = "".join(f'<li><span class="meta">{esc(x["房間"])}｜{esc(x["時間"])}｜{esc(x["誰"])}</span><br>{esc(x["內容"])}</li>' for x in w) or "<li class=meta>沒有人在等你回 🎉</li>"
    tl = "".join(f'<li><b>#{esc(str(x["id"]))}</b> {esc(x["標題"])} <span class="meta">（{esc(x["狀態"])}）</span><br><span class="meta">{esc(x["最後"])}</span></li>' for x in todos) or "<li class=meta>沒有負責中的待辦</li>"
    wk = "".join(f'<li><span class="meta">{esc(r.get("時間", ""))}｜訂閱「{esc(r.get("關鍵字", ""))}」｜{esc(r.get("結果", ""))}</span><br>{esc(r.get("原因", ""))}</li>' for r in wakes) or "<li class=meta>還沒有派報紀錄</li>"
    fd = "".join(f'<details><summary><b>{esc(x["誰"])}</b> <span class="meta">{esc(x["時間"])}</span>　{esc(x["內容"].splitlines()[0][:60])}</summary><div class="pre">{esc(x["內容"])}</div></details>' for x in feed(hub))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>訊息版</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--acc:#c96442}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:900px;margin:0 auto;padding:12px;overflow-x:hidden}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 14px;margin:10px 0}}
h3{{margin:4px 0 6px;font-size:16px}} .meta{{color:var(--mute);font-size:12px}} li{{margin:6px 0;font-size:14px;overflow-wrap:anywhere}} ul{{padding-left:18px}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap}} .tabs a{{padding:3px 12px;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:var(--ink)}} .tabs a.sel{{background:var(--ink);color:var(--card)}}
.pre{{white-space:pre-wrap;font-size:13px;padding:4px 0 8px}} details{{border-top:1px solid var(--line);padding:4px 0}} summary{{cursor:pointer;font-size:14px}}
</style></head><body>
<div class="tabs">{tabs}</div>
<section><h3>① 等 {esc(who)} 處理（{len(w) + len(todos)}）</h3>
<p class="meta">被 @ 之後還沒在那個房間回話的（24 小時內）；負責中的待辦。做完才會消失，看過不算。</p>
<ul>{wl}</ul>{"".join(f'<h3 style="font-size:14px">🆘 求救卡</h3><ul>' + "".join(f'<li><b>{esc(c["id"])}</b>（{esc(c.get("等級", ""))}，{esc(c.get("發起", ""))} 發起，第 {c.get("次數", 1)} 次）<br>{esc(c.get("欄位", {}).get("卡在", ""))}</li>' for c in sos) + "</ul>" for _ in [0] if sos)}<h3 style="font-size:14px">負責中的待辦</h3><ul>{tl}</ul></section>
<section><h3>② {esc(who)} 追蹤的</h3><p class="meta">派報生的「下一期」新聞（小 AI 判斷不急的）與最近叫醒紀錄。訂閱規則在設定頁。</p>
<div class="pre">{esc(news) or "（下一期還沒有新聞）"}</div><ul>{wk}</ul></section>
<section><h3>③ 全部動態（機器人群，新的在上，同一人連續的收在一起）</h3>{fd or "<p class=meta>沒有</p>"}</section>
</body></html>"""
