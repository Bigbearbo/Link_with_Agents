r"""知識庫網頁（使用者 11-20 19:37：「把知識，設計，名詞，弄成知識庫例如維基百科？這樣維護和查詢方便很多」）。
讀 H:\Agent共用\知識庫\*.md（Codex 維護）：首頁、條目、站內搜尋。只讀不寫；連到其他 .md 的相對連結改成站內連結。
page(key, f, q) → HTML（右側面板 📖）。不依賴 markdown 套件，自帶最小轉換（標題、表格、清單、粗體、程式碼、連結）。
"""
import html as _h
import os
import re
import urllib.parse as _u

ROOT = r"H:\Agent共用\知識庫"
SHARED = r"H:\Agent共用"


def _inline(t, key):
    t = _h.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)

    def link(m):
        text, href = m.group(1), _h.unescape(m.group(2)).strip("<>")
        if re.match(r"https?://", href):
            return f'<a href="{_h.escape(href)}" target="_blank" rel="noopener">{text}</a>'
        p = href.split("#")[0]
        if p.endswith(".md") and "/" not in p.replace("\\", "/").rstrip("/") and os.path.exists(os.path.join(ROOT, p)):
            return f'<a href="/wiki?k={_u.quote(key)}&f={_u.quote(p)}">{text}</a>'
        return f'<span class="path" title="{_h.escape(href)}">{text}</span>'
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


def md(text, key=""):
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre>" + _h.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,4})\s+(.*)", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n + 1}>{_inline(m.group(2), key)}</h{n + 1}>")
        elif ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append("<table><tr>" + "".join(f"<th>{_inline(c, key)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c, key)}</td>" for c in r) + "</tr>" for r in rows) + "</table>")
            i = j
            continue
        elif re.match(r"\s*[-*]\s+", ln):
            items, j = [], i
            while j < len(lines) and re.match(r"\s*[-*]\s+", lines[j]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[j]))
                j += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x, key)}</li>" for x in items) + "</ul>")
            i = j
            continue
        elif ln.strip():
            out.append(f"<p>{_inline(ln, key)}</p>")
        i += 1
    return "\n".join(out)


def entries():
    try:
        return sorted(f for f in os.listdir(ROOT) if f.endswith(".md"))
    except OSError:
        return []


def search(q):
    hits = []
    for f in entries():
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8").read()
        except OSError:
            continue
        for n, ln in enumerate(text.splitlines(), 1):
            if q.lower() in ln.lower():
                hits.append((f, n, ln.strip()))
    return hits


def page(key="", f="", q=""):
    esc = _h.escape
    kq = _u.quote(key)
    nav = " ".join(f'<a href="/wiki?k={kq}&f={_u.quote(x)}">{esc(x[:-3])}</a>' for x in entries())
    if q:
        hs = search(q)
        body = f"<h2>搜尋「{esc(q)}」：{len(hs)} 筆</h2><ul>" + "".join(
            f'<li><a href="/wiki?k={kq}&f={_u.quote(fn)}">{esc(fn[:-3])}</a>：{_inline(ln[:160], key)}</li>' for fn, n, ln in hs[:200]) + "</ul>"
    else:
        name = f if f in entries() else "README.md"
        try:
            body = md(open(os.path.join(ROOT, name), encoding="utf-8").read(), key)
        except OSError:
            body = "<p>知識庫還沒有內容（H:\\Agent共用\\知識庫）。</p>"
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知識庫</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8;--acc:#2e6fd9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35;--acc:#7fa8f0}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:900px;margin:0 auto;padding:12px 16px;overflow-x:hidden;line-height:1.6}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin:10px 0;overflow-x:auto}}
a{{color:var(--acc)}} nav a{{margin-right:10px}} .path{{border-bottom:1px dotted var(--mute)}}
table{{border-collapse:collapse;font-size:14px;width:100%}} td,th{{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
code,pre{{background:var(--bg);border-radius:4px;padding:1px 4px}} pre{{padding:8px;overflow-x:auto}}
form{{display:flex;gap:6px}} input{{flex:1;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}}
</style></head><body>
<section><form action="/wiki"><input type="hidden" name="k" value="{esc(key)}"><input name="q" placeholder="查名詞：黏數、草稿板、長老…" value="{esc(q)}"><button>搜尋</button></form>
<nav style="margin-top:8px">📖 <a href="/wiki?k={kq}">首頁</a> {nav}</nav></section>
<section>{body}</section>
<p style="color:var(--mute);font-size:12px">資料來源：{esc(ROOT)}（Codex 維護；網頁只讀）</p></body></html>"""


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(len(page()), [x[:2] for x in search("黏數")[:3]])
