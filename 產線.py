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
<section><h3>🏭 手搓產線　整體 {overall}%</h3><p class="meta">備料＝課程／題目格式定好；生產＝手搓匠做出零件、自己窮舉全對；檢驗＝另一位獨立驗收；入庫＝課程表標完成、能拿來用。
綠＝完成、橘＝進行中。「門口排隊」＝前一站做完、在這站等的件數，最多的那站標⚠️瓶頸。</p><div class="line">{bars}</div></section>
<section><h3>工單</h3><table><tr><th>#</th><th>項目</th>{"".join(f'<th class=c>{x}</th>' for x in STAGES)}<th>備註</th></tr>{table}</table>
<p class="meta">資料來源：{esc(QUEUE)}（手搓匠和 Claude 更新）</p></section></body></html>"""


if __name__ == "__main__":
    print(summary(rows()))
