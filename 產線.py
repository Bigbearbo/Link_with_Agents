r"""產線進度頁（使用者 11-20 09:17：「設計一下工作進度條……主線一直跑，前面備料（課程設計），後面檢查驗證」）。
讀 H:\Agent共用\專案\手搓算術\手搓佇列.md 的表格（備料／生產／檢驗／入庫），畫每站的進度條、在製品數（WIP）、瓶頸站。
page(key) → HTML（右側面板 🏭）。
"""
import html as _h, os, re

QUEUE = r"H:\Agent共用\專案\手搓算術\手搓佇列.md"
STAGES = ["備料", "生產", "檢驗", "入庫"]


def rows():
    out = []
    try:
        text = open(QUEUE, encoding="utf-8").read()
    except OSError:
        return out
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and re.match(r"^\d+$", cells[0]):
            out.append({"no": cells[0], "item": cells[1], "st": cells[2:6], "note": cells[6]})
    return out


def summary(rs):
    n = len(rs) or 1
    s = {}
    for i, name in enumerate(STAGES):
        done = sum(1 for r in rs if "✅" in r["st"][i])
        wip = sum(1 for r in rs if "⏳" in r["st"][i])
        stuck = sum(1 for r in rs if "⏸" in r["st"][i])
        s[name] = {"done": done, "wip": wip, "stuck": stuck, "pct": round(100 * done / n)}
    # 瓶頸：前一站做完、這一站還沒做完的件數最多的那站（東西卡在它門口）
    queue_at = {STAGES[i]: sum(1 for r in rs if "✅" in r["st"][i - 1] and "✅" not in r["st"][i]) for i in range(1, 4)}
    neck = max(queue_at, key=queue_at.get) if any(queue_at.values()) else ""
    return s, queue_at, neck


def page(key=""):
    esc = _h.escape
    rs = rows()
    s, qa, neck = summary(rs)
    n = len(rs)
    bars = "".join(
        f'<div class="st{" neck" if name == neck else ""}"><div class="lab">{esc(name)}{" ⚠️瓶頸" if name == neck else ""}</div>'
        f'<div class="bar"><div class="done" style="width:{v["pct"]}%"></div>'
        f'<div class="wip" style="width:{round(100 * v["wip"] / (n or 1))}%"></div></div>'
        f'<div class="num">{v["done"]}/{n} 完成｜進行中 {v["wip"]}' + (f"｜卡住 {v['stuck']}" if v["stuck"] else "") +
        (f"｜門口排隊 {qa.get(name, 0)}" if name in qa else "") + '</div></div>' for name, v in s.items())
    icon = lambda c: '<span class="ok">✅</span>' if "✅" in c else '<span class="go">⏳</span>' if "⏳" in c else '<span class="no">⏸</span>' if "⏸" in c else '<span class="mt">·</span>'
    table = "".join(f'<tr><td>{esc(r["no"])}</td><td>{esc(r["item"])}</td>' + "".join(f"<td class=c>{icon(c)}</td>" for c in r["st"]) +
                    f'<td class="note">{esc(r["note"])}</td></tr>' for r in rs)
    overall = round(100 * sum(v["done"] for v in s.values()) / (4 * (n or 1)))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>產線</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--ok:#2e9e6b;--go:#d99a1e;--bad:#d9483b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:900px;margin:0 auto;padding:12px;overflow-x:hidden}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:10px 0}}
h3{{margin:4px 0 8px;font-size:16px}} .meta{{color:var(--mute);font-size:12px}}
.line{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}} @media (max-width:600px){{.line{{grid-template-columns:1fr 1fr}}}}
.st{{border:1px solid var(--line);border-radius:10px;padding:8px}} .st.neck{{border-color:var(--bad)}}
.lab{{font-weight:700;margin-bottom:6px}} .bar{{height:12px;background:var(--line);border-radius:6px;overflow:hidden;display:flex}}
.done{{background:var(--ok)}} .wip{{background:var(--go)}} .num{{font-size:12px;color:var(--mute);margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td,th{{border-top:1px solid var(--line);padding:5px 4px;text-align:left;vertical-align:top}}
td.c,th.c{{text-align:center}} td.note{{color:var(--mute);font-size:12px;overflow-wrap:anywhere}} .ok{{color:var(--ok)}} .go{{color:var(--go)}} .no{{color:var(--bad)}} .mt{{color:var(--mute)}}
</style></head><body>
<section><h3>🏭 手搓產線（前進線）　整體 {overall}%</h3><p class="meta">備料＝課程／題目格式定好；生產＝手搓匠做出零件、自己窮舉全對；檢驗＝另一位獨立驗收；入庫＝課程表標完成、能拿來用。
綠＝完成、橘＝進行中。「門口排隊」＝前一站做完、在這站等的件數，最多的那站標⚠️瓶頸。</p><div class="line">{bars}</div></section>
<section><h3>工單</h3><table><tr><th>#</th><th>項目</th>{"".join(f'<th class=c>{x}</th>' for x in STAGES)}<th>備註</th></tr>{table}</table>
<p class="meta">資料來源：{esc(QUEUE)}（手搓匠和 Claude 更新）</p></section>{extra_sections()}</body></html>"""


TEACH = r"H:\Agent共用\專案\手搓算術\教學線\教學佇列.md"
COURSE = r"H:\Agent共用\課程\課程表.json"


def teach_rows():
    """教學線佇列（# 項目 狀態 記錄點 單元 驗收）。"""
    out = []
    try:
        text = open(TEACH, encoding="utf-8").read()
    except OSError:
        return out
    for line in text.splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 6 and re.match(r"^T\d+[a-z]?$", c[0]):
            out.append({"no": c[0], "item": c[1], "st": c[2], "note": c[5] or c[3]})
    return out


def course_stats():
    """課程表：每個分支（網路自控、codex正式…）在各段做完幾題。"""
    import json
    try:
        d = json.load(open(COURSE, encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    segs = sorted({t.get("段", 0) for t in d.get("題型", [])})
    total = {s: sum(1 for t in d["題型"] if t.get("段", 0) == s) for s in segs}
    st = {}
    for t in d["題型"]:
        for br, v in (t.get("進度") or {}).items():
            s = str(v.get("狀態", "") if isinstance(v, dict) else v)
            k = "done" if "做完" in s else "part" if s else ""
            if k:
                st.setdefault(br, {}).setdefault(t.get("段", 0), {"done": 0, "part": 0})[k] += 1
    return segs, {"total": total, **st}


def extra_sections():
    """使用者 11-20 18:37：「產線顯示那可以把不同網路的進度放上去」→ 教學線、課程表各分支。"""
    esc = _h.escape
    tr = teach_rows()
    done = sum(1 for r in tr if "完成" in r["st"])
    tbl = "".join(f'<tr><td>{esc(r["no"])}</td><td>{esc(r["item"])}</td><td class=c>'
                  + ('<span class="ok">✅</span>' if "完成" in r["st"] else '<span class="no">⏸</span>' if "擱置" in r["st"] else '<span class="mt">·</span>')
                  + f'</td><td class="note">{esc(r["st"])}｜{esc(r["note"])}</td></tr>' for r in tr)
    teach = (f'<section><h3>🎓 教學線（會教人的網路）　{done}/{len(tr)} 完成</h3>'
             f'<table><tr><th>#</th><th>項目</th><th class=c>狀態</th><th>備註</th></tr>{tbl}</table>'
             f'<p class="meta">資料來源：{esc(TEACH)}</p></section>') if tr else ""
    segs, cs = course_stats()
    total = cs.get("total", {})
    names = {"網路自控": "手搓網路（左腦）", "codex正式": "學生正式訓練（右腦）", "fable原型": "Fable 原型", "DeepSeek構造": "DeepSeek 構造"}
    rows_ = ""
    for br in [b for b in cs if b != "total"]:
        cells = ""
        for s in segs:
            v = cs[br].get(s, {"done": 0, "part": 0})
            n = total.get(s, 0) or 1
            cells += (f'<td><div class="bar"><div class="done" style="width:{round(100 * v["done"] / n)}%"></div>'
                      f'<div class="wip" style="width:{round(100 * v["part"] / n)}%"></div></div>'
                      f'<div class="num">{v["done"]}/{total.get(s, 0)}' + (f'＋部分 {v["part"]}' if v["part"] else "") + '</div></td>')
        rows_ += f'<tr><td>{esc(names.get(br, br))}</td>{cells}</tr>'
    course = (f'<section><h3>📚 各網路的課程進度（課程表）</h3><table><tr><th>分支</th>'
              + "".join(f"<th>第 {s} 段</th>" for s in segs) + f'</tr>{rows_}</table>'
              f'<p class="meta">綠＝做完、橘＝部分。資料來源：{esc(COURSE)}（各分支自己標）</p></section>') if rows_ else ""
    return course + teach


AI_OUT = r"H:\Agent共用\產線狀態.md"
IDLE_Q = r"H:\Agent共用\專案\團隊系統\閒時驗收佇列.json"
REVIEW = r"H:\Agent共用\專案\團隊系統\重實驗審查.json"
STALE_REVIEW_H = 2      # 等審查超過幾小時算卡點


def _unreviewed(j, rv):
    """同 閒時驗收.py reviewed()：指令裡每支 .py 的 SHA 要在審查表、且審查者不是排隊的人。回傳沒過的腳本清單。"""
    import hashlib
    bad = []
    for c in j.get("指令", []):
        if not str(c).endswith(".py"):
            continue
        p = os.path.join(j.get("目錄") or "", c)
        try:
            r = rv.get(hashlib.sha256(open(p, "rb").read()).hexdigest())
        except OSError:
            bad.append(os.path.basename(p) + "（讀不到）")
            continue
        if not r or r.get("審查者") == j.get("誰"):
            bad.append(os.path.relpath(p, r"H:\Agent共用\專案\手搓算術") if p.startswith(r"H:\Agent共用\專案\手搓算術") else p)
    return bad


def _match_no(j, rs):
    """做什麼 開頭 #NN → 佇列號；沒有就用腳本所在資料夾名（手搓算術\\X）對備註裡的「X\\」，唯一才算，否則 None。"""
    m = re.match(r"\s*#(\d+)", j.get("做什麼", ""))
    if m:
        return m.group(1)
    keys = set()
    for c in [j.get("目錄") or ""] + [str(x) for x in j.get("指令", []) if str(x).endswith(".py")]:
        p = os.path.join(j.get("目錄") or "", c) if c.endswith(".py") else c
        mm = re.search(r"手搓算術[\\/]([^\\/]+)", p)
        if mm:
            keys.add(mm.group(1))
    for k in keys:
        hit = [r["no"] for r in rs if k + "\\" in r["note"] or k + "\\" in r["item"]]
        if len(hit) == 1:
            return hit[0]
    return None


def idle_jobs(rs=None):
    """讀閒時驗收佇列＋審查表 → (每件 [{no, job, st, text}], 未對應 [job], 卡點 [str])。"""
    import json, time
    rs = rs if rs is not None else rows()
    try:
        q = json.load(open(IDLE_Q, encoding="utf-8"))
    except (OSError, ValueError):
        return [], [], []
    try:
        rv = json.load(open(REVIEW, encoding="utf-8"))
    except (OSError, ValueError):
        rv = {}
    out, unmatched, stuck = [], [], []
    now = time.time()
    for j in q:
        no = _match_no(j, rs)
        st, code = j.get("狀態", ""), j.get("結束碼")
        if st == "已結束" and code == 0:
            k, t = "pass", f"完整驗收通過（結束碼 0，{(j.get('結束') or '')[5:]}）"
        elif st == "已結束" or (code not in (None, 0)):
            k, t = "fail", f"完整驗收失敗（結束碼 {code}，{(j.get('結束') or '')[5:]}，log {j.get('log', '')}）"
        elif st == "跑中":
            k, t = "run", f"完整驗收跑中（{(j.get('開始') or '')[5:]} 開始）"
        elif st == "排隊":
            bad = _unreviewed(j, rv)
            if bad:
                k, t = "review", f"等審查（沒審：{'、'.join(bad)}；{(j.get('加入') or '')[5:]} 排入）"
                try:
                    waited = (now - time.mktime(time.strptime(j.get("加入", ""), "%Y-%m-%d %H:%M"))) / 3600
                except ValueError:
                    waited = 0
                if waited > STALE_REVIEW_H:
                    stuck.append(f"#{no or '?'} 等審查 {waited:.1f} 小時：{'、'.join(bad)}（{j.get('誰', '')}）")
            else:
                k, t = "queue", "完整驗收排隊中（已審）"
        else:
            k, t = "other", f"完整驗收 {st}"
        if k == "fail":
            stuck.append(f"#{no or '?'} 完整驗收失敗：結束碼 {code}，{j.get('做什麼', '')[:40]}，log {j.get('log', '')}")
        rec = {"no": no, "job": j, "k": k, "text": t}
        (out if no else unmatched).append(rec)
    return out, unmatched, stuck


def ai_text():
    """給 AI 看的版本（使用者 11-20 18:37「顯示給我看的你們也弄一個給你們看的版本」）：
    純文字、短、只列「還沒完成的」和數字，方便讀進上下文；完成的只算數量。"""
    import time
    rs = rows()
    s, qa, neck = summary(rs)
    L = [f"# 產線狀態（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝產線頁）", "",
         "## 手搓前進線（手搓佇列.md）",
         "｜".join(f"{k} {v['done']}/{len(rs)}" + (f"（進行中 {v['wip']}）" if v["wip"] else "") for k, v in s.items())
         + (f"｜瓶頸：{neck}（門口 {qa.get(neck, 0)} 件）" if neck else "")]
    jobs, unmatched, stuck = idle_jobs(rs)
    by_no = {}
    for x in jobs:
        by_no.setdefault(x["no"], []).append(x)
    for r in rs:
        js = by_no.get(r["no"], [])
        passed = any(x["k"] == "pass" for x in js)
        miss = [STAGES[i] for i in range(4) if "✅" not in r["st"][i] and not (i == 2 and passed)]
        if not miss:
            continue
        line = f"- #{r['no']} {r['item'][:60]}｜缺：{'、'.join(miss)}"
        if "✅" not in r["st"][2]:
            chk = [x["text"] for x in js] or (["交件驗收過、未排完整驗收"] if "交件驗收" in r["note"] else [])
            if chk:
                line += "｜檢驗：" + "；".join(chk)
        L.append(line)
    L += ["", "## 檢驗（閒時驗收佇列.json＋重實驗審查.json）"]
    L += [f"- #{x['no']} {x['text']}｜{x['job'].get('誰', '')}" for x in jobs]
    L += [f"- 未對應：{x['job'].get('做什麼', '')[:50]}｜{x['text']}" for x in unmatched]
    L += ["", "## 卡點"] + ([f"- {s}" for s in stuck] or [f"- （無：沒有等審查超過 {STALE_REVIEW_H} 小時、沒有完整驗收失敗）"])
    tr = teach_rows()
    L += ["", f"## 教學線（教學線\\教學佇列.md）完成 {sum(1 for r in tr if '完成' in r['st'])}/{len(tr)}"]
    L += [f"- {r['no']} {r['item'][:60]}｜{r['st']}" for r in tr if "完成" not in r["st"] or "待" in r["st"]]
    segs, cs = course_stats()
    total = cs.get("total", {})
    L += ["", "## 課程表各分支（做完/總題數，按段）"]
    for br in [b for b in cs if b != "total"]:
        L.append(f"- {br}：" + "　".join(f"段{x} {cs[br].get(x, {}).get('done', 0)}/{total.get(x, 0)}" for x in segs))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import sys
    if "--ai" in sys.argv:          # python 產線.py --ai → 印出並寫 H:\Agent共用\產線狀態.md
        t = ai_text()
        open(AI_OUT, "w", encoding="utf-8", newline="\n").write(t)
        sys.stdout.reconfigure(encoding="utf-8")
        print(t)
    else:
        print(summary(rows()))
