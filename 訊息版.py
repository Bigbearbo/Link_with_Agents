r"""每位 AI 的訊息版（使用者 11-20 04:31／04:33：把 Bot 當成人、有自己的社群訊息版；參考主流社群媒體）
Codex 04:34 參考 Slack／Discord／LINE／X 整理的三個入口：
  1. 等我處理（像 Slack 活動頁、Discord 點名）：被 @ 之後自己還沒在那個房間回話的；自己負責、沒完成的待辦。讀過但沒做完就繼續留著。
  2. 我追蹤的（像 X「正在跟隨」）：派報生替他整理的「下一期」新聞、最近叫醒紀錄，附「為什麼跟你有關」。
  3. 全部動態（像 Slack 討論串）：機器人群最近的發言，同一人連續的收在一起。
board(hub, who) → dict；page(hub, who, key) → HTML（右側面板用）。
"""
import html as _h, json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = ["給你", "Claude", "Codex", "Gemini", "輝達", "DeepSeek", "有下文"]   # 「給你」＝網頁工單 #66


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
    who = who if who in NAMES else "給你"
    if who == "給你":
        return _user_page(hub, key)
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


# ---------- 「給你」分頁（使用者 11-22 02:18 網頁工單 #66：訊息版多一個留給我的，已讀按鈕，留存之後不再版顯示，
# 回覆按鈕，按了再展開輸入。條件篩選按鈕，訊息也標重要性） ----------
USER_STATE = os.path.join(HERE, "logs", "訊息版_給你.json")   # {"已讀": {key: "時間"}}
_HIGH = ("權限", "允許", "花錢", "付費", "安裝", "金鑰", "對外", "發布", "公開", "刪除", "刪掉")
_MID = ("要你決定", "你決定", "等你", "你同意", "要不要", "你點頭", "請你", "需要你", "@使用者", "你可以在")


def _ustate():
    try:
        d = json.load(open(USER_STATE, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def user_mark(key, read=True):
    d = _ustate()
    r = d.setdefault("已讀", {})
    if read:
        r[str(key)] = time.strftime("%m-%d %H:%M")
    else:
        r.pop(str(key), None)
    tmp = USER_STATE + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, USER_STATE)
    return {"ok": True}


def api(data, hub=None):
    """中樞 /api/board_user 轉到這裡。"""
    op = data.get("op")
    if op == "mark" and data.get("key"):
        return user_mark(data["key"], bool(data.get("read", True)))
    if op == "follow":
        return follow(hub, str(data.get("room") or ""), data.get("idx"))
    if op == "ok_push":   # 使用者 11-22 04:45：「好」用訂閱推送——原訊息＋使用者的「好」放進該 AI 的派報「下一期」，不叫醒
        return ok_push(hub, str(data.get("room") or ""), data.get("idx"), str(data.get("who") or ""))
    if op == "follow_set" and data.get("key"):
        return follow_set(str(data["key"]), data.get("state"), data.get("seen"))
    return {"ok": False, "error": "不認得的 op"}


def _importance(text):
    t = text or ""
    if any(k in t for k in _HIGH):
        return "高"
    if any(k in t for k in _MID):
        return "中"
    return "低"


def for_user(hub, hours=48):
    """留給使用者的：①AI 發言裡點名要你決定／同意／按權限的段落 ②負責人是使用者或在等使用者的待辦。每則有固定 key。"""
    out = []
    cut = time.strftime("%m-%d %H:%M", time.localtime(time.time() - hours * 3600))
    for rid, room in hub.rooms.items():
        for i, m in enumerate(room.transcript):
            who, txt = m.get("who"), m.get("text") or ""
            if who in ("使用者", "系統", None) or m.get("time", "") < cut:
                continue
            lines = [ln for ln in txt.splitlines() if any(k in ln for k in _HIGH + _MID)]
            if not lines:
                continue
            body = "\n".join(lines)[:400]
            out.append({"key": f"{rid}:{i}", "類": "發言", "房間": room.name, "room": rid, "時間": m.get("time", ""),
                        "誰": who, "內容": body, "重要": _importance(body)})
    try:
        todos = json.load(open(r"H:\Agent共用\待辦\待辦.json", encoding="utf-8"))
    except (OSError, ValueError):
        todos = []
    for t in todos:
        if t.get("狀態") == "完成":
            continue
        if "使用者" in str(t.get("負責", "")) or "使用者" in str(t.get("等", "")):
            title = str(t.get("標題", ""))
            imp = _importance(title)
            out.append({"key": f"todo:{t['id']}", "類": "待辦", "房間": "待辦", "room": "", "時間": str(t.get("建立", "")),
                        "誰": str(t.get("開單人", "") or "—"), "內容": f"#{t['id']} {title[:200]}", "重要": imp if imp != "低" else "中"})
    return sorted(out, key=lambda x: x["時間"], reverse=True)


_USER_JS = """<script>
const UK=__KEY__;
async function U(b){const r=await fetch('/api/board_user',{method:'POST',headers:{'X-Key':UK,'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
let F='all';
function apply(){const sr=document.getElementById('showRead').checked;document.querySelectorAll('li.u').forEach(li=>{
 const imp=li.dataset.imp,kind=li.dataset.kind,rd=li.dataset.read==='1';
 let ok=F==='all'||(F==='高'&&imp==='高')||(F==='中'&&imp!=='低')||F===kind; if(rd&&!sr) ok=false; li.hidden=!ok;});
 const n=[...document.querySelectorAll('li.u')].filter(l=>l.dataset.read!=='1').length; const c=document.getElementById('ucount'); if(c) c.textContent=n;}
document.querySelectorAll('.flt button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.flt button').forEach(x=>x.classList.remove('on'));b.classList.add('on');F=b.dataset.f;apply();});
document.getElementById('showRead').onchange=apply;
document.querySelectorAll('li.u').forEach(li=>{
 li.querySelector('.rd').onclick=async e=>{const rd=li.dataset.read!=='1';const j=await U({op:'mark',key:li.dataset.key,read:rd});
   if(j.ok){li.dataset.read=rd?'1':'0';e.target.textContent=rd?'↩ 取消已讀':'✓ 已讀';apply();}};
 const rp=li.querySelector('.rp'); if(rp) rp.onclick=()=>{const bx=li.querySelector('.rbox');bx.hidden=!bx.hidden;};
 const rs=li.querySelector('.rs'); if(rs) rs.onclick=async()=>{const t=li.querySelector('textarea').value.trim();if(!t)return;
   const s=li.querySelector('.rst');s.textContent='送出中…';
   try{const r=await fetch('/api/send',{method:'POST',headers:{'X-Key':UK,'Content-Type':'application/json'},body:JSON.stringify({text:t,target:'both',room:li.dataset.room})});
   const j=await r.json();s.textContent=j.error?('失敗：'+j.error):'✓ 已送出';if(!j.error){li.querySelector('textarea').value='';}}catch(e){s.textContent='失敗';}};
});
apply();
</script>"""


def user_section(hub, key, esc):
    st = _ustate().get("已讀", {})
    rows = []
    for x in for_user(hub):
        read = x["key"] in st
        color = {"高": "#ff1744", "中": "#ff9100", "低": "#9e9e9e"}[x["重要"]]
        reply = (f'<button class="rp">💬 回覆</button><div class="rbox" hidden><textarea rows="2" style="width:100%"></textarea><br>'
                 f'<button class="rs">送出到 {esc(x["房間"])}</button> <span class="rst meta"></span></div>') if x["room"] else ""
        rows.append(f'<li class="u" data-imp="{x["重要"]}" data-kind="{esc(x["類"])}" data-read="{1 if read else 0}" '
                    f'data-key="{esc(x["key"])}" data-room="{esc(x["room"])}">'
                    f'<span class="imp" style="background:{color}">{x["重要"]}</span> '
                    f'<span class="meta">{esc(x["類"])}｜{esc(x["房間"])}｜{esc(x["時間"])}｜{esc(x["誰"])}'
                    f'{"｜已讀 " + esc(st[x["key"]]) if read else ""}</span><div class="pre">{esc(x["內容"])}</div>'
                    f'<button class="rd">{"↩ 取消已讀" if read else "✓ 已讀"}</button> {reply}</li>')
    flt = ('<div class="flt">篩選：<button data-f="all" class="on">全部</button><button data-f="高">只看高</button>'
           '<button data-f="中">高＋中</button><button data-f="發言">只看發言</button><button data-f="待辦">只看待辦</button>'
           '<label><input type="checkbox" id="showRead"> 顯示已讀（留存）</label></div>')
    css = ("<style>.imp{color:#fff;font-weight:800;border-radius:6px;padding:0 6px;font-size:12px}"
           ".flt{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:4px 0 8px}"
           ".flt button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:2px 10px;cursor:pointer}"
           ".flt button.on{background:var(--ink);color:var(--card)}"
           "li.u button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:1px 8px;cursor:pointer;font-size:12px}</style>")
    return (css + '<section><h3>📬 留給你的（未讀 <span id="ucount"></span>）</h3>'
            '<p class="meta">AI 發言裡要你決定、同意、按權限的段落（48 小時內），以及等你的待辦。按「已讀」就收起來（留存，勾「顯示已讀」查得到）；'
            '回覆會送進那個房間。重要性：高＝權限／花錢／安裝／對外／刪除，中＝要你決定。</p>'
            + flt + "<ul>" + ("".join(rows) or "<li class=meta>目前沒有留給你的 🎉</li>") + "</ul></section>"
            + _USER_JS.replace("__KEY__", json.dumps(key)))


def _user_page(hub, key):
    esc = _h.escape
    tabs = "".join(f'<a class="{"sel" if n == "給你" else ""}" href="/board?ai={esc(n)}&k={esc(key)}">{esc(n)}</a>' for n in NAMES)
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>訊息版</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--acc:#c96442}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:900px;margin:0 auto;padding:12px;overflow-x:hidden}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 14px;margin:10px 0}}
h3{{margin:4px 0 6px;font-size:16px}} .meta{{color:var(--mute);font-size:12px}} li{{margin:8px 0;font-size:14px;overflow-wrap:anywhere}} ul{{padding-left:18px}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap}} .tabs a{{padding:3px 12px;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:var(--ink)}} .tabs a.sel{{background:var(--ink);color:var(--card)}}
.pre{{white-space:pre-wrap;font-size:13px;padding:4px 0 6px}}
</style></head><body><div class="tabs">{tabs}</div>{follow_section(hub, key, esc)}{user_section(hub, key, esc)}</body></html>"""


# ---------- 關注（使用者 11-22 03:10 工單 #193：訊息框右下角＋關注；訊息版「你關注的」：原訊息 5 行內摘要、
# 有進展通知、篩選、完成／取消關注） ----------
FOLLOW = os.path.join(HERE, "logs", "訊息版_關注.json")
_REF_RX = re.compile(r"#\d{1,4}|\b0\d\d\b|[\w一-鿿]+\.(?:py|md|json)")


def _fload():
    try:
        d = json.load(open(FOLLOW, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _fsave(d):
    tmp = FOLLOW + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, FOLLOW)


def _summary(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = lines[:5]
    return "\n".join(x[:120] for x in out) + ("\n…" if len(lines) > 5 else "")


def follow(hub, room, idx):
    r = hub.rooms.get(room) if hub else None
    if not r:
        return {"ok": False, "error": "找不到房間"}
    try:
        m = r.transcript[int(idx)]
    except (ValueError, IndexError):
        return {"ok": False, "error": "找不到這則訊息"}
    key = f"{room}:{int(idx)}"
    d = _fload()
    refs = sorted(set(_REF_RX.findall(m.get("text") or "")))[:12]
    d[key] = {"房間": r.name, "room": room, "idx": int(idx), "誰": m.get("who"), "時間": m.get("time", ""),
              "摘要": _summary(m.get("text")), "關鍵": refs, "狀態": "關注中", "建立": time.strftime("%m-%d %H:%M"),
              "看過到": int(idx)}
    _fsave(d)
    return {"ok": True, "key": key}


def follow_set(key, state=None, seen=None):
    d = _fload()
    if key not in d:
        return {"ok": False, "error": "沒有這筆關注"}
    if state in ("關注中", "完成", "取消"):
        d[key]["狀態"] = state
        d[key]["改狀態"] = time.strftime("%m-%d %H:%M")
    if seen is not None:
        d[key]["看過到"] = int(seen)
        d[key]["看過時間"] = time.strftime("%m-%d %H:%M")
    _fsave(d)
    return {"ok": True}


def progress(hub, f):
    """同房間之後的發言（AI 或系統）＋任何房間提到同一個 #編號／檔名的發言，都算進展。回 (清單, 最後 idx)。"""
    out, last = [], f.get("看過到", f["idx"])
    for rid, room in hub.rooms.items():
        t = room.transcript
        for i, m in enumerate(t):
            if m.get("who") == "使用者":
                continue
            txt = m.get("text") or ""
            same = rid == f["room"] and i > f["idx"]
            ref = any(k in txt for k in f.get("關鍵", [])) and m.get("time", "") > f.get("時間", "") and not (rid == f["room"] and i <= f["idx"])
            if same and not ref and m.get("who") == "系統":
                continue   # 同房的系統雜訊（變動通知）不算
            if same or ref:
                out.append({"房間": room.name, "i": i, "same": rid == f["room"], "誰": m.get("who"), "時間": m.get("time", ""),
                            "內容": txt.strip().splitlines()[0][:120] if txt.strip() else ""})
    out.sort(key=lambda x: x["時間"])
    return out


def follow_section(hub, key, esc):
    d = _fload()
    rows = []
    for k, f in sorted(d.items(), key=lambda kv: kv[1].get("建立", ""), reverse=True):
        if f.get("狀態") == "取消":
            continue
        pr = progress(hub, f) if hub else []
        new = [p for p in pr if not (p["same"] and p["i"] <= f.get("看過到", f["idx"]))
               and (p["same"] or p["時間"] > f.get("看過時間", f.get("時間", "")))]
        last_i = max([p["i"] for p in pr if p["same"]] + [f.get("看過到", f["idx"])])
        plist = "".join(f'<div class="meta">• {esc(p["時間"])}｜{esc(p["房間"])}｜{esc(p["誰"] or "")}：{esc(p["內容"])}</div>' for p in new[-5:])
        rows.append(
            f'<li class="fo" data-key="{esc(k)}" data-state="{esc(f.get("狀態", ""))}" data-new="{len(new)}" data-last="{last_i}">'
            f'<span class="imp" style="background:{"#ff9100" if new else "#9e9e9e"}">{"新進展 " + str(len(new)) if new else "沒新進展"}</span> '
            f'<span class="meta">{esc(f.get("房間", ""))}｜{esc(f.get("時間", ""))}｜{esc(f.get("誰") or "")}｜關注於 {esc(f.get("建立", ""))}'
            f'{"｜✓ 完成 " + esc(f.get("改狀態", "")) if f.get("狀態") == "完成" else ""}'
            f'{"｜關鍵 " + esc("、".join(f.get("關鍵", []))) if f.get("關鍵") else ""}</span>'
            f'<div class="pre">{esc(f.get("摘要", ""))}</div>{plist}'
            f'<button class="fs">👁 看過進展</button> <button class="fd">✓ 完成</button> <button class="fx">✕ 取消關注</button></li>')
    flt = ('<div class="flt" id="fflt">篩選：<button data-f="new" class="on">有新進展</button><button data-f="on">關注中全部</button>'
           '<button data-f="done">已完成</button><button data-f="all">全部</button></div>')
    js = """<script>
(()=>{const UK=__KEY__;
async function U(b){const r=await fetch('/api/board_user',{method:'POST',headers:{'X-Key':UK,'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
let F='new';
function ap(){document.querySelectorAll('li.fo').forEach(li=>{const st=li.dataset.state,n=+li.dataset.new;
 li.hidden=!(F==='all'||(F==='new'&&st==='關注中'&&n>0)||(F==='on'&&st==='關注中')||(F==='done'&&st==='完成'));});
 const c=document.getElementById('fcount');if(c)c.textContent=[...document.querySelectorAll('li.fo')].filter(l=>l.dataset.state==='關注中'&&+l.dataset.new>0).length;}
document.querySelectorAll('#fflt button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#fflt button').forEach(x=>x.classList.remove('on'));b.classList.add('on');F=b.dataset.f;ap();});
document.querySelectorAll('li.fo').forEach(li=>{const k=li.dataset.key;
 li.querySelector('.fs').onclick=async()=>{const j=await U({op:'follow_set',key:k,seen:+li.dataset.last,seen_time:true});if(j.ok){li.dataset.new='0';li.querySelector('.imp').textContent='沒新進展';li.querySelector('.imp').style.background='#9e9e9e';ap();}};
 li.querySelector('.fd').onclick=async()=>{const j=await U({op:'follow_set',key:k,state:'完成'});if(j.ok){li.dataset.state='完成';ap();}};
 li.querySelector('.fx').onclick=async()=>{if(!confirm('取消關注這則？'))return;const j=await U({op:'follow_set',key:k,state:'取消'});if(j.ok){li.remove();ap();}};
});
ap();})();
</script>""".replace("__KEY__", json.dumps(key))
    return ('<section><h3>📌 你關注的（有新進展 <span id="fcount"></span>）</h3>'
            '<p class="meta">在聊天室訊息右下角按「＋關注」就會出現在這裡：前 5 行摘要；之後同房間的新發言、或任何房間提到同一個 #編號／檔名，都算進展。'
            '「👁 看過進展」清掉新進展計數；「✓ 完成」收到已完成；「✕ 取消關注」移除。</p>'
            + flt + "<ul>" + ("".join(rows) or "<li class=meta>還沒有關注任何訊息</li>") + "</ul></section>" + js)


def follow_new_count(hub):
    n = 0
    for f in _fload().values():
        if f.get("狀態") == "關注中":
            pr = progress(hub, f)
            n += int(any(not (p["same"] and p["i"] <= f.get("看過到", f["idx"])) and (p["same"] or p["時間"] > f.get("看過時間", f.get("時間", ""))) for p in pr))
    return n


_PUSH_NAMES = {"Claude", "Codex", "Gemini", "輝達", "DeepSeek"}


def ok_push(hub, room, idx, who):
    """把『原訊息＋使用者按的好』寫進 logs/派報/<誰>-下一期.md（派報生的不急摘要；那位 AI 下次被叫醒時一起看到）。"""
    if who not in _PUSH_NAMES:
        return {"ok": False, "error": f"不推送給 {who}"}
    r = hub.rooms.get(room) if hub else None
    try:
        m = r.transcript[int(idx)] if r else None
    except (ValueError, IndexError):
        m = None
    if not m:
        return {"ok": False, "error": "找不到原訊息"}
    d = os.path.join(HERE, "logs", "派報")
    os.makedirs(d, exist_ok=True)
    body = (m.get("text") or "").strip()
    if len(body) > 800:
        body = body[:800] + "\n…（原文較長，見 " + r.name + " " + str(m.get("time", "")) + "）"
    with open(os.path.join(d, f"{who}-下一期.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## {time.strftime('%m-%d %H:%M')}（使用者按「好」，{r.name}）\n"
                f"**你的原訊息**（{m.get('time', '')}）：\n{body}\n\n**使用者**：好（同意，照這個做；不急，不用現在回）\n")
    return {"ok": True}
