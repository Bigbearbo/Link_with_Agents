# -*- coding: utf-8 -*-
"""白板頁（使用者 11-26 22:34：「白板幫我放在上方條（待討論那裡）」）。
只讀 H:\\Agent共用\\白板\\*.md，轉成簡單 HTML（標題、表格、清單、程式碼、mermaid 流程圖）。每次請求重讀檔，不用重開中樞。"""
import html
import os
import re

DIR = r"H:\Agent共用\白板"


def _inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return s


def _render(md):
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            lang = ln[3:].strip()
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            body = html.escape("\n".join(buf))
            out.append(f'<pre class="mermaid">{body}</pre>' if lang == "mermaid" else f"<pre><code>{body}</code></pre>")
            i = j + 1
            continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            t = "<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>"
            t += "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append('<div class="tw">' + t + "</table></div>")
            i = j
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n + 1}>{_inline(m.group(2))}</h{n + 1}>")
        elif ln.startswith(">"):
            out.append(f"<blockquote>{_inline(ln.lstrip('> '))}</blockquote>")
        elif re.match(r"^\s*[-*]\s+", ln):
            item = re.sub(r"^\s*[-*]\s+", "", ln)
            out.append("<li>" + _inline(item) + "</li>")
        elif re.match(r"^\s*\d+\.\s+", ln):
            out.append(f"<li class='n'>{_inline(ln.strip())}</li>")
        elif ln.strip() == "---":
            out.append("<hr>")
        elif ln.strip():
            out.append(f"<p>{_inline(ln)}</p>")
        i += 1
    return "\n".join(out)


def page(key="", f=""):
    try:
        files = sorted(x for x in os.listdir(DIR) if x.endswith(".md"))
    except OSError:
        files = []
    cur = f if f in files else (files[0] if files else "")
    tabs = " ".join(f'<a class="{"on" if x == cur else ""}" href="?k={html.escape(key)}&f={html.escape(x)}">{html.escape(x[:-3])}</a>' for x in files)
    body = "<p>還沒有白板。</p>"
    if cur:
        try:
            body = _render(open(os.path.join(DIR, cur), encoding="utf-8").read())
        except OSError as e:
            body = f"<p>讀不到：{html.escape(str(e))}</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>白板</title>
<style>
:root{{--bg:#fbfbf8;--fg:#1d1f24;--mut:#666b75;--line:#dcdde2;--acc:#2f6f8f;--code:#f0f1f3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17191d;--fg:#e6e7ea;--mut:#9aa0aa;--line:#33363d;--acc:#7fb6d2;--code:#22252b}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.6 system-ui,"Microsoft JhengHei",sans-serif;margin:0;padding:12px 16px}}
h2,h3,h4,h5{{margin:18px 0 6px}} h2{{font-size:20px}} h3{{font-size:17px;border-bottom:1px solid var(--line);padding-bottom:3px}}
.tabs a{{margin-right:10px;color:var(--mut);text-decoration:none}} .tabs a.on{{color:var(--acc);font-weight:600}}
.tw{{overflow-x:auto}} table{{border-collapse:collapse;margin:6px 0}} th,td{{border:1px solid var(--line);padding:4px 8px;vertical-align:top}}
th{{background:var(--code)}} code{{background:var(--code);padding:1px 4px;border-radius:3px}} pre{{background:var(--code);padding:8px;overflow-x:auto}}
blockquote{{border-left:3px solid var(--acc);margin:6px 0;padding:2px 10px;color:var(--mut)}} li{{margin-left:18px}} li.n{{list-style:none}}
.note{{color:var(--mut);font-size:12px}}
</style></head><body>
<div class="tabs">{tabs}</div>
<p class="note">檔案：{html.escape(os.path.join(DIR, cur))}（要改直接改這個檔，重新整理就會更新）</p>
{body}
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>try{{mermaid.initialize({{startOnLoad:true,theme:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'default')}})}}catch(e){{}}</script>
</body></html>"""
