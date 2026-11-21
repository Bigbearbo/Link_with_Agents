r"""中樞可調參數（使用者 11-21 01:35：「一些參數可以放到本房或設定那裡，那些頁面也顯示給你們，用程式排版成給你們看的輸出」）。

一份 中樞參數.json 存值；中樞每次用到時讀（改檔就生效，不用重開）。
- 網頁「設定」頁有一張表顯示（給人看）。
- python 中樞參數.py --ai → 寫 H:\Agent共用\中樞參數.md（給 AI 看：值、意思、誰定的、何時）。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "中樞參數.json")
AI_OUT = r"H:\Agent共用\中樞參數.md"

DEFAULTS = {
    "接力換棒秒數": {"值": 60, "單位": "秒", "說明": "接力時某一棒超過這麼久還沒講完，下一棒先開始；兩棒都卡住才再往下",
                "來源": "使用者 11-21 01:23"},
    "AI點名接力上限": {"值": 4, "單位": "次", "說明": "每則使用者訊息後，AI 之間用 @名字 互相點名最多接力幾次",
                  "來源": "既有（MAX_HOPS）"},
    "Codex額度保護": {"值": 0.95, "單位": "週額度比例", "說明": "Codex 週額度用到這個比例以上，群聊名單先不叫它（@Codex 照叫）",
                   "來源": "使用者 11-20 11:51；11-20 16:10 說之後可改線性公式"},
}

_cache = {"mtime": None, "data": {}}


def load():
    try:
        mt = os.path.getmtime(PATH)
        if mt != _cache["mtime"]:
            _cache["data"] = json.load(open(PATH, encoding="utf-8"))
            _cache["mtime"] = mt
        data = _cache["data"]
    except (OSError, ValueError):
        data = {}
    out = {}
    for k, d in DEFAULTS.items():
        v = dict(d)
        if isinstance(data.get(k), dict) and "值" in data[k]:
            v.update(data[k])
        out[k] = v
    return out


def get(name):
    return load()[name]["值"]


def ensure_file():
    if not os.path.exists(PATH):
        json.dump({k: dict(v, 更新=time.strftime("%m-%d %H:%M")) for k, v in DEFAULTS.items()},
                  open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def ai_text():
    L = [f"# 中樞參數（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝設定頁；值存在 H:\\Claude用\\三方討論\\中樞參數.json，改檔即生效）", ""]
    for k, v in load().items():
        L.append(f"- **{k}** ＝ {v['值']} {v.get('單位', '')}：{v.get('說明', '')}（{v.get('來源', '')}；更新 {v.get('更新', '—')}）")
    L += ["", "使用者可在網頁「⚙️ 設定・本房」用拉條直接改（來源會記「使用者（網頁）」）。AI 有意見：在討論房提，寫明想改哪個、改成多少、理由。"]
    return "\n".join(L) + "\n"


# 網頁拉條可改的範圍（使用者 11-22 00:37：「這個參數給我可以直接改，用拉條，選完按設定儲存，範圍0~240」）
# 名稱: (最小, 最大, 步進, 整數?)
RANGES = {   # 使用者 11-22 01:35 網頁工單 #61：改下拉選單，接力換棒 0～240 間隔 20
    "接力換棒秒數": (0, 240, 20, True),
    "AI點名接力上限": (0, 10, 1, True),
    "Codex額度保護": (0.50, 1.00, 0.01, False),
}


def set_values(values, who="使用者（網頁）", path=None, ai_out=None):
    """驗範圍後寫 中樞參數.json，並重寫給 AI 讀的 .md。回 {"ok": True, "changed": {...}} 或 {"ok": False, "error": ...}；有錯就整批不寫。"""
    path = path or PATH
    ai_out = AI_OUT if ai_out is None else ai_out
    if not isinstance(values, dict) or not values:
        return {"ok": False, "error": "沒有要改的參數"}
    clean = {}
    for k, raw in values.items():
        if k not in RANGES:
            return {"ok": False, "error": f"不能從網頁改：{k}"}
        lo, hi, step, is_int = RANGES[k]
        if isinstance(raw, bool):
            return {"ok": False, "error": f"{k} 要是數字"}
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{k} 要是數字：{raw!r}"}
        if v != v or not (lo <= v <= hi):
            return {"ok": False, "error": f"{k} 超出範圍 {lo}～{hi}：{raw}"}
        if is_int:
            if v != int(v):
                return {"ok": False, "error": f"{k} 要是整數：{raw}"}
            v = int(v)
        else:
            v = round(v, 2)
        clean[k] = v
    try:
        data = json.load(open(path, encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    now = time.strftime("%m-%d %H:%M")
    changed = {}
    for k, v in clean.items():
        ent = dict(DEFAULTS[k], **(data.get(k) if isinstance(data.get(k), dict) else {}))
        if ent.get("值") == v:
            continue
        changed[k] = {"舊": ent.get("值"), "新": v}
        ent.update({"值": v, "來源": f"{who} {now}", "更新": now})
        data[k] = ent
    if changed:
        tmp = path + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        if path == PATH:
            _cache["mtime"] = None
        if ai_out:
            try:
                txt = ai_text() if path == PATH else ai_text_from(path)
                open(ai_out, "w", encoding="utf-8", newline="\n").write(txt)
            except OSError:
                pass
    return {"ok": True, "changed": changed}


def ai_text_from(path):
    """測試用：用指定的 json 檔產生 ai_text（不動快取）。"""
    global PATH
    old, oc = PATH, dict(_cache)
    try:
        PATH = path
        _cache["mtime"] = None
        return ai_text()
    finally:
        PATH = old
        _cache.update(oc)
        _cache["mtime"] = None


def html_form(esc):
    """設定頁的參數表：可改的每列一條拉條＋目前值，底部「儲存設定」→ POST /api/params。頁面要先定義 const K（網頁鑰匙）。"""
    rows = []
    for k, v in load().items():
        if k in RANGES:
            lo, hi, step, _ = RANGES[k]
            n = int(round((hi - lo) / step))
            opts = [round(lo + i * step, 2) for i in range(n + 1)]
            cur = v["值"]
            if cur not in opts:   # 目前值不在格點上（例 90）也要選得到，不然一存就被改掉
                opts = sorted(opts + [cur])
            fmt = (lambda x: str(int(x))) if RANGES[k][3] else (lambda x: f"{x:.2f}")
            ctl = (f'<select class="prm" data-name="{esc(k)}" style="font-size:15px;padding:3px 6px">'
                   + "".join(f'<option value="{fmt(o)}"{" selected" if o == cur else ""}>{fmt(o)}</option>' for o in opts)
                   + f'</select> {esc(v.get("單位", ""))}<br><span class="meta">範圍 {lo}～{hi}，間隔 {step}</span>')
        else:
            ctl = f'{esc(str(v["值"]))} {esc(v.get("單位", ""))}'
        rows.append(f"<tr><td><b>{esc(k)}</b></td><td style='min-width:220px'>{ctl}</td>"
                    f"<td>{esc(v.get('說明', ''))}</td><td class='meta'>{esc(v.get('來源', ''))}<br>更新 {esc(v.get('更新', '—'))}</td></tr>")
    return ("<table><tr><td class='meta'>參數</td><td class='meta'>值</td><td class='meta'>意思</td><td class='meta'>誰定的</td></tr>"
            + "".join(rows) + "</table>"
            '<p><button onclick="saveParams()">儲存設定</button> <span id="prmStatus" class="meta"></span></p>'
            "<script>async function saveParams(){const s=document.getElementById('prmStatus'),v={};"
            "document.querySelectorAll('.prm').forEach(x=>v[x.dataset.name]=Number(x.value));s.textContent='存檔中…';"
            "try{const r=await fetch('/api/params',{method:'POST',headers:{'X-Key':K,'Content-Type':'application/json'},body:JSON.stringify({values:v})});"
            "const j=await r.json();if(j.ok){const n=Object.keys(j.changed||{}).length;s.textContent=n?'✓ 已存（改了 '+n+' 項，下次用到就生效）':'沒有變動';}"
            "else s.textContent='失敗：'+(j.error||'');}catch(e){s.textContent='失敗';}}</script>")


def html_rows(esc):
    return "".join(f"<tr><td><b>{esc(k)}</b></td><td>{esc(str(v['值']))} {esc(v.get('單位', ''))}</td>"
                   f"<td>{esc(v.get('說明', ''))}</td><td class='meta'>{esc(v.get('來源', ''))}</td></tr>"
                   for k, v in load().items())


if __name__ == "__main__":
    import sys
    ensure_file()
    if "--ai" in sys.argv:
        t = ai_text()
        open(AI_OUT, "w", encoding="utf-8", newline="\n").write(t)
        sys.stdout.reconfigure(encoding="utf-8")
        print(t)
