"""待審區（工單 #270，使用者 11-23 06:07 同意）：AI 寫到沒權限的地方不中斷，改存這裡，使用者或 Claude 一鍵放行／退回。
存放：H:\\Agent共用\\待審區\\<名字>\\<原目標相對共用區的路徑>\\檔名，旁邊 檔名.meta.json（誰、時間、原目標、原因、目標當時 mtime）。
放行：目標必須在 H:\\Agent共用 底下；H:\\金鑰、H:\\Claude用 永遠拒；寫入前再比目標 mtime 與送件時一樣（不一樣＝要求重新審）。
給 AI 的指令：python H:\\Agent共用\\專案\\團隊系統\\待審送件.py <名字> <目標路徑> <暫存檔> [原因]"""
import os, json, time, shutil, difflib, subprocess, html as _h

SHARE = os.environ.get("PENDING_SHARE", r"H:\Agent共用")          # 測試時改指向暫存複本
FORBID = [r"H:\金鑰", r"H:\Claude用"]                          # 永遠拒，不受測試覆蓋
ROOT = os.path.join(SHARE, "待審區")
DONE, REJ = "_已放行", "_已退回"


def _n(p):
    return os.path.normcase(os.path.abspath(os.path.realpath(p)))


def _under(p, base):
    p, base = _n(p), _n(base)
    return p == base or p.startswith(base.rstrip("\\/") + os.sep)


def check_target(target):
    """回 (ok, 原因)。只准 SHARE 底下；金鑰／Claude用／待審區自己／.git 一律拒。"""
    if not target or "\0" in target:
        return False, "目標空白"
    for f in FORBID:
        if _under(target, f):
            return False, f"目標在 {f}（永遠拒）"
    if not _under(target, SHARE):
        return False, f"目標不在 {SHARE} 底下"
    if _under(target, ROOT):
        return False, "目標不能在待審區自己裡面"
    if ".git" in _n(target).split(os.sep):
        return False, "目標不能在 .git 裡"
    return True, ""


def _mt(p):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return None


def submit(name, target, src, reason=""):
    name = "".join(c for c in str(name) if c not in '\\/:*?"<>|').strip() or "unknown"
    ok, why = check_target(target)
    if not ok:
        return {"ok": False, "error": why}
    if not os.path.isfile(src):
        return {"ok": False, "error": f"暫存檔不存在：{src}"}
    rel = os.path.relpath(_n(target), _n(SHARE))
    dst = os.path.join(ROOT, name, rel)
    if os.path.exists(dst):   # 同人同目標再送＝覆蓋舊件（以最新為準）
        pass
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    meta = {"誰": name, "時間": time.time(), "原目標": os.path.abspath(target), "原因": str(reason)[:500],
            "目標mtime_ns": _mt(target), "目標存在": os.path.exists(target)}
    json.dump(meta, open(dst + ".meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"ok": True, "id": os.path.relpath(dst, ROOT).replace("\\", "/")}


def _items():
    out = []
    if not os.path.isdir(ROOT):
        return out
    for d, ds, fs in os.walk(ROOT):
        ds[:] = [x for x in ds if x not in (DONE, REJ)]
        for f in fs:
            if f.endswith(".meta.json"):
                continue
            p = os.path.join(d, f)
            if os.path.exists(p + ".meta.json"):
                out.append(p)
    return out


def _read(p):
    try:
        return open(p, "rb").read()
    except OSError:
        return None


def _diff(new_p, target, limit=200):
    a, b = _read(target), _read(new_p)
    if b is None:
        return "（讀不到待審件）"
    try:
        bt = b.decode("utf-8")
        at = a.decode("utf-8") if a is not None else ""
    except UnicodeDecodeError:
        return "（二進位檔，無法預覽差異）"
    lines = list(difflib.unified_diff(at.splitlines(), bt.splitlines(), "目標現況" if a is not None else "（新檔）", "待審版", lineterm=""))
    more = len(lines) - limit
    return "\n".join(lines[:limit]) + (f"\n…（還有 {more} 行）" if more > 0 else "") if lines else "（內容與目標現況相同）"


def list_items():
    res = []
    for p in sorted(_items()):
        try:
            m = json.load(open(p + ".meta.json", encoding="utf-8"))
        except Exception:
            m = {}
        tgt = m.get("原目標", "")
        stale = m.get("目標mtime_ns") != _mt(tgt) if tgt else True
        res.append({"id": os.path.relpath(p, ROOT).replace("\\", "/"), "誰": m.get("誰", ""), "時間": m.get("時間", 0),
                    "原目標": tgt, "原因": m.get("原因", ""), "目標已變": stale, "差異": _diff(p, tgt) if tgt else ""})
    return res


def _find(id_):
    p = os.path.join(ROOT, *str(id_).replace("\\", "/").split("/"))
    if not _under(p, ROOT) or any(x in (DONE, REJ) for x in str(id_).replace("\\", "/").split("/")[:1]):
        return None
    return p if os.path.isfile(p) and os.path.exists(p + ".meta.json") else None


def _archive(p, bucket, extra=None):
    rel = os.path.relpath(p, ROOT)
    dst = os.path.join(ROOT, bucket, time.strftime("%m%d-%H%M%S"), rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(p, dst)
    m = json.load(open(p + ".meta.json", encoding="utf-8"))
    if extra:
        m.update(extra)
    json.dump(m, open(dst + ".meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.remove(p + ".meta.json")
    return dst


def approve(id_, who="使用者"):
    p = _find(id_)
    if not p:
        return {"ok": False, "error": "找不到這件"}
    m = json.load(open(p + ".meta.json", encoding="utf-8"))
    tgt = m.get("原目標", "")
    ok, why = check_target(tgt)
    if not ok:
        return {"ok": False, "error": "拒絕放行：" + why}
    if _mt(tgt) != m.get("目標mtime_ns"):
        return {"ok": False, "error": "目標檔在送審後被改過，請重新審（退回後請作者重送）", "stale": True}
    os.makedirs(os.path.dirname(tgt), exist_ok=True)
    shutil.copy2(p, tgt)
    msg = ""
    if os.path.isdir(os.path.join(SHARE, ".git")):
        g = ["git", "-C", SHARE, "-c", "core.quotepath=false", "-c", "user.name=Claude", "-c", "user.email=claude@local"]
        fl = 0x08000000 if os.name == "nt" else 0
        rel = os.path.relpath(tgt, SHARE)
        subprocess.run(g + ["add", "-f", "--", rel], capture_output=True, creationflags=fl)
        r = subprocess.run(g + ["commit", "-m", f"待審區放行：{rel}（作者 {m.get('誰')}，放行 {who}）", "--", rel], capture_output=True, creationflags=fl)
        msg = "已 git 記錄" if r.returncode == 0 else "git 沒新變化或失敗"
    _archive(p, DONE, {"放行者": who, "放行時間": time.time()})
    return {"ok": True, "msg": msg}


def reject(id_, who="使用者", reason=""):
    p = _find(id_)
    if not p:
        return {"ok": False, "error": "找不到這件"}
    _archive(p, REJ, {"退回者": who, "退回原因": str(reason)[:500], "退回時間": time.time()})
    return {"ok": True}


def api(data):
    op = data.get("op")
    who = str(data.get("who") or "使用者")[:30]
    try:
        if op == "list":
            return {"ok": True, "items": list_items()}
        if op == "approve":
            return approve(data.get("id"), who)
        if op == "reject":
            return reject(data.get("id"), who, data.get("reason") or "")
        if op == "submit":
            return submit(data.get("name"), data.get("target"), data.get("src"), data.get("reason") or "")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": "未知 op"}


def count():
    try:
        return len(_items())
    except Exception:
        return 0


def page(key=""):
    esc = _h.escape
    fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "—"
    its = list_items()
    cards = "".join(
        f"<section><b>{esc(i['誰'])}</b> → <code>{esc(i['原目標'])}</code> <span class=m>{fmt(i['時間'])}</span>"
        + (" <b style='color:#c0392b'>⚠ 目標在送審後被改過，放行會被拒</b>" if i["目標已變"] else "")
        + f"<br><span class=m>原因：{esc(i['原因'] or '—')}</span><pre>{esc(i['差異'])}</pre>"
        f"<button onclick='act(\"approve\",{json.dumps(i['id'])})'>✅ 放行</button> "
        f"<button onclick='act(\"reject\",{json.dumps(i['id'])})'>↩ 退回</button></section>" for i in its) or "<p>目前沒有待審件 🎉</p>"
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>待審區</title><style>
:root{{--bg:#f4f2ee;--card:#fff;--ink:#1d1b18;--mute:#7a756d;--line:#e4e0d8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#17171a;--card:#202024;--ink:#ecebe8;--mute:#9a968f;--line:#2f2f35}}}}
body{{background:var(--bg);color:var(--ink);font-family:system-ui,"Microsoft JhengHei",sans-serif;max-width:960px;margin:0 auto;padding:12px 16px;overflow-x:hidden}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:10px 0}}
pre{{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;overflow-x:auto;max-height:360px;font-size:12px}}
.m{{color:var(--mute);font-size:12px}} button{{padding:4px 12px}}
</style></head><body><h3>🛂 待審區（{len(its)}）</h3>
<p class=m>AI 寫不到的地方會存在這裡。差異＝待審版對目標檔現況（前 200 行）。只有 H:\\Agent共用 底下的檔能放行。放行者：<input id=who value="使用者" style="width:120px"></p>
{cards}
<script>const K={json.dumps(key)};
async function act(op,id){{let reason='';if(op==='reject'){{reason=prompt('退回原因？（可留空）')||'';}}
const r=await fetch('/api/review',{{method:'POST',headers:{{'X-Key':K,'Content-Type':'application/json'}},body:JSON.stringify({{op,id,reason,who:document.getElementById('who').value||'使用者'}})}});
const j=await r.json();if(j.ok)location.reload();else alert('失敗：'+(j.error||''));}}
</script></body></html>"""
