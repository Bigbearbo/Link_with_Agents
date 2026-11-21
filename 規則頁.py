r"""「規則」頁（使用者 11-22 01:38 網頁工單 #62：按鈕「規則」兩字；規則＋團隊目標條列式顯示，最上最下各一個新增按鈕，
每條一個提意見修改按鈕）。

資料來源（只讀，原檔不改）：
- H:\Agent共用\新成員入門.md 的「## 4. 規則」整節（大家要守的規則）
- H:\Agent共用\團隊目標.md（工作方法、G0～G3…，每個 ## 一組）
按「新增」或「提意見」不會直接改規則檔，而是開一張工單（/api/errors op=ticket，開單人＝使用者（網頁）），
由 AI 討論後改正本；這樣改動有紀錄、也不會被一按就蓋掉。
"""
import json
import os
import re

SRC_RULES = r"H:\Agent共用\新成員入門.md"
SRC_GOALS = r"H:\Agent共用\團隊目標.md"


def _read(p):
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


def _items(block):
    """markdown 片段 → [(層級, 文字)]：只取「- 」「* 」「1. 」開頭的條目與表格以外的普通段落第一行。"""
    out = []
    for ln in block.splitlines():
        m = re.match(r"^(\s*)(?:[-*]|\d+[.)])\s+(.*\S)", ln)
        if m:
            out.append((len(m.group(1)) // 2, m.group(2)))
        elif ln.strip().startswith(">"):
            out.append((0, ln.strip().lstrip("> ").strip()))
    return out


def _sections(text, only=None):
    """依 ## 切；only＝只要標題含這個字串的節。回 [(標題, [(層級, 文字)])]。"""
    parts = re.split(r"^## +(.+)$", text, flags=re.M)
    secs = []
    for i in range(1, len(parts), 2):
        title, body = parts[i].strip(), parts[i + 1]
        if only and only not in title:
            continue
        # ### 小節也當成條目的標題
        sub = re.split(r"^### +(.+)$", body, flags=re.M)
        items = _items(sub[0])
        for j in range(1, len(sub), 2):
            items.append((0, "【" + sub[j].strip() + "】"))
            items += [(lv + 1, t) for lv, t in _items(sub[j + 1])]
        if items:
            secs.append((title, items))
    return secs


def _md_inline(esc, s):
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def page(key=""):
    import html
    esc = html.escape
    groups = [("規則（新成員入門 §4）", SRC_RULES, _sections(_read(SRC_RULES), only="規則")),
              ("團隊目標", SRC_GOALS, _sections(_read(SRC_GOALS)))]
    n = 0
    body = []
    for gname, src, secs in groups:
        body.append(f"<h2>{esc(gname)}</h2><p class='meta'>正本：{esc(src)}（這頁只讀；改動經由工單）</p>")
        if not secs:
            body.append("<p class='meta'>讀不到內容</p>")
        for title, items in secs:
            lis = []
            for lv, t in items:
                n += 1
                ref = f"{gname}｜{title}｜{t[:60]}"
                lis.append(f"<li style='margin-left:{lv * 18}px'><span>{_md_inline(esc, t)}</span> "
                           f"<button class='pi' data-ref='{esc(ref)}' title='對這條提意見或要修改'>✏️ 提意見</button></li>")
            body.append(f"<section><h3>{esc(title)}</h3><ul>{''.join(lis)}</ul></section>")
    add = "<p><button class='add'>➕ 新增規則／目標</button> <span class='st meta'></span></p>"
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>規則</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--acc:#2e6fd9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35;--acc:#7fa8f0}}}}
body{{background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,"Microsoft JhengHei",sans-serif;margin:0;padding:10px 16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 14px;margin:10px 0}}
h2{{margin:14px 0 2px}} h3{{margin:4px 0}} ul{{padding-left:18px;margin:4px 0}} li{{margin:3px 0}}
.meta{{color:var(--mute);font-size:12px}} code{{background:rgba(128,128,128,.15);padding:0 3px;border-radius:4px}}
button{{cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:2px 8px}}
button.add{{background:#2e6fd9;color:#fff;border:0;font-weight:700;padding:5px 12px}}
button.pi{{font-size:12px;padding:0 6px;opacity:.75}} button.pi:hover{{opacity:1}}
</style></head><body>
<h1>📜 規則</h1><p class="meta">共 {n} 條。按「新增」或「提意見」會開一張工單給 AI 處理（開單人＝你），討論後才改正本。</p>
{add}{''.join(body)}{add}
<script>const K={json.dumps(key)};
async function ticket(title, desc){{
  const r = await fetch('/api/errors', {{method:'POST', headers:{{'X-Key':K,'Content-Type':'application/json'}},
    body: JSON.stringify({{op:'ticket', who:'使用者', title, desc, pri:'中', to:'自動派', level:''}})}});
  return r.json();
}}
function status(btn, t){{ const s = btn.parentElement.querySelector('.st') || btn.nextElementSibling; if(s) s.textContent = t; else btn.title = t; }}
document.querySelectorAll('button.add').forEach(b => b.onclick = async () => {{
  const t = prompt('新增哪一條規則或目標？（會開成工單，AI 討論後寫進正本）'); if(!t) return;
  try{{ const j = await ticket('規則提案：新增「' + t.slice(0,40) + '」', '使用者在規則頁新增：' + t); status(b, j.ok ? '✓ 已開工單 #' + j.id : '失敗：' + (j.error||'')); }}catch(e){{ status(b, '失敗'); }}
}});
document.querySelectorAll('button.pi').forEach(b => b.onclick = async () => {{
  const ref = b.dataset.ref; const t = prompt('對這條的意見或想怎麼改：\\n' + ref); if(!t) return;
  try{{ const j = await ticket('規則意見：' + ref.split('｜').slice(-1)[0].slice(0,30), '條目：' + ref + '\\n意見（使用者）：' + t);
    b.textContent = j.ok ? '✓ #' + j.id : '失敗'; }}catch(e){{ b.textContent = '失敗'; }}
}});
</script></body></html>"""


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s = page("x")
    print(len(s), s.count("class='pi'"), "條")
