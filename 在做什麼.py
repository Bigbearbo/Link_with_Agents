r"""產線頁最前面「誰在做什麼」（使用者 11-23 00:50 工單 #269）：每位 AI（含子代理、小號）目前任務／工單、狀態、最後動靜。
只讀狀態，不叫醒、不打擾 AI；網頁每 60 秒向 /api/working 拿一次（伺服器端快取 30 秒）。
資料來源：
- 中樞各房間的 agent 忙碌狀態與目前步驟（hub 物件）、視窗 AI 的 群組\狀態.json（room.window_progress）
- H:\Agent共用\待辦\待辦.json：各負責人「進行中」的工單與最後紀錄時間
- terminal\子代理.json：終端機 Claude 派出去的子代理（`python 在做什麼.py 派 名稱 "任務" [--單 N]`／`完 名稱`）
- 小號程序：命令列含 H:\Codex小號\小號.py 或 H:\輝達小號\小號.py 的 python 程序，任務名取 --name
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUBS = os.path.join(HERE, "terminal", "子代理.json")
TODO = r"H:\Agent共用\待辦\待辦.json"
_cache = {"t": 0, "v": None}
_proc_cache = {"t": 0, "v": []}


def _load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_subs(d):
    tmp = SUBS + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, SUBS)


def _small_procs():
    """跑中的小號（Codex 小號／輝達小號）：60 秒查一次程序表。"""
    if time.time() - _proc_cache["t"] < 60:
        return _proc_cache["v"]
    out = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                            "Where-Object { $_.CommandLine -match '小號\\.py' } | ForEach-Object { $_.CommandLine }"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        for line in r.stdout.splitlines():
            who = "Codex 小號" if "Codex小號" in line else "輝達小號" if "輝達小號" in line else None
            if not who:
                continue
            m = re.search(r"--name\s+(\S+)", line)
            out.append({"who": who, "task": m.group(1) if m else "（沒給任務名）"})
    except Exception:
        pass
    _proc_cache.update(t=time.time(), v=out)
    return out


def _todos():
    """負責人 → 進行中的工單（最多 3 張）＋最後紀錄時間。"""
    by = {}
    for t in _load(TODO, []):
        if t.get("狀態") not in ("進行中", "等"):
            continue
        whos = [w.strip() for w in re.split(r"[、,＋+／/]", str(t.get("負責") or "")) if w.strip()]
        rec = t.get("紀錄") or []
        last = ""
        if rec:
            x = rec[-1]
            last = x.get("時間", "") if isinstance(x, dict) else (re.match(r"\d\d-\d\d \d\d:\d\d", str(x)) or [""])[0]
        last = last or t.get("建立", "")
        for who in whos:
            by.setdefault(who, []).append({"id": t["id"], "title": str(t.get("標題", ""))[:60], "state": t.get("狀態"), "last": last})
    for v in by.values():
        v.sort(key=lambda x: x["last"], reverse=True)
    return by


def collect(hub):
    if _cache["v"] is not None and time.time() - _cache["t"] < 30:
        return _cache["v"]
    rows, now = {}, time.time()

    def row(who):
        return rows.setdefault(who, {"who": who, "kind": "AI", "state": "閒置", "doing": "", "elapsed": None, "rooms": [], "tickets": []})

    for name in ("Claude", "Codex", "Gemini", "輝達", "DeepSeek"):
        row(name)
    if hub:
        for rid, room in list(hub.rooms.items()):
            try:
                for a in room.agents():
                    if a.busy:
                        r = row(a.name)
                        r["state"] = "工作中"
                        r["doing"] = (list(a.steps)[-1] if a.steps else "工作中")[:80]
                        r["elapsed"] = int(now - (a.started or now))
                        r["rooms"].append(room.name)
                for w in room.window_progress():
                    r = row(w["who"])
                    r.update(state="工作中", doing=(w["steps"][-1] if w.get("steps") else "工作中")[:80], elapsed=w.get("elapsed"))
                    r["rooms"].append(room.name)
            except Exception:
                continue
    subs = _load(SUBS, {})
    for name, s in subs.items():
        if s.get("狀態") == "完成":
            continue
        r = row(name)
        r.update(kind="子代理", state="工作中", doing=s.get("任務", "")[:80], elapsed=int(now - s.get("開始", now)))
        if s.get("單"):
            r["tickets"].append({"id": s["單"], "title": "", "state": "進行中", "last": ""})
    for p in _small_procs():
        r = row(p["who"])
        r.update(kind="小號", state="工作中", doing=p["task"])
    for who in ("Codex 小號", "輝達小號"):
        rows.setdefault(who, {"who": who, "kind": "小號", "state": "閒置", "doing": "", "elapsed": None, "rooms": [], "tickets": []})
    td = _todos()
    for who, lst in td.items():
        if not who or who in ("自動派", "待分類"):
            continue
        r = row(who)
        if who == "使用者":
            r["kind"] = "使用者"
        r["tickets"] = (r["tickets"] + lst)[:3]
        if r["state"] == "閒置":
            r["state"] = "有單待接"
    for r in rows.values():
        if r["state"] != "工作中" and r["tickets"]:
            last = max((t["last"] for t in r["tickets"] if t["last"]), default="")
            if last and last < time.strftime("%m-%d %H:%M", time.localtime(now - 6 * 3600)):
                r["state"] = "卡住？"   # 進行中的單超過 6 小時沒紀錄
    order = {"工作中": 0, "卡住？": 1, "有單待接": 2, "閒置": 3}
    v = {"time": time.strftime("%H:%M:%S"), "rows": sorted(rows.values(), key=lambda r: (order.get(r["state"], 9), r["kind"] != "AI", r["who"]))}
    _cache.update(t=now, v=v)
    return v


SECTION = """<section id="working"><h3>👥 誰在做什麼 <span class="meta" id="wk-t"></span></h3>
<p class="meta">每 60 秒自動刷新；只讀狀態，不會叫醒或打擾 AI。工作中＝正在跑；有單待接＝有進行中的工單但現在沒在跑；卡住？＝進行中的單超過 6 小時沒紀錄。</p>
<table id="wk"><tr><th>誰</th><th>狀態</th><th>正在做</th><th>工單</th></tr></table></section>
<script>
(()=>{const K=__KEY__;const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const col={"工作中":"#2e9e6b","卡住？":"#d9483b","有單待接":"#d99a1e","閒置":"#9e9e9e"};
const dur=s=>s==null?"":(s<60?s+" 秒":s<3600?Math.round(s/60)+" 分":(s/3600).toFixed(1)+" 小時");
async function load(){try{const r=await fetch('/api/working?k='+encodeURIComponent(K));const d=await r.json();
document.getElementById('wk-t').textContent='更新 '+d.time;
document.getElementById('wk').innerHTML='<tr><th>誰</th><th>狀態</th><th>正在做</th><th>工單</th></tr>'+d.rows.map(x=>
'<tr><td><b>'+esc(x.who)+'</b><div class="meta">'+esc(x.kind)+(x.rooms.length?'｜'+esc(x.rooms.join('、')):'')+'</div></td>'+
'<td><span style="color:#fff;background:'+(col[x.state]||'#9e9e9e')+';border-radius:6px;padding:0 6px;font-size:12px;font-weight:700">'+esc(x.state)+'</span>'+
(x.elapsed!=null?'<div class="meta">'+dur(x.elapsed)+'</div>':'')+'</td>'+
'<td class="note">'+esc(x.doing)+'</td>'+
'<td class="note">'+x.tickets.map(t=>'#'+t.id+(t.title?' '+esc(t.title):'')+(t.last?'（'+esc(t.last)+'）':'')).join('<br>')+'</td></tr>').join('');
}catch(e){document.getElementById('wk-t').textContent='讀取失敗';}}
load();setInterval(load,60000);})();
</script>"""


def section(key):
    return SECTION.replace("__KEY__", json.dumps(key))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    d = _load(SUBS, {})
    if a[:1] == ["派"] and len(a) >= 3:
        d[a[1]] = {"任務": a[2], "開始": time.time(), "狀態": "工作中",
                   "單": int(a[a.index("--單") + 1]) if "--單" in a else None}
        _save_subs(d); print("登記", a[1])
    elif a[:1] == ["完"] and len(a) >= 2:
        if a[1] in d:
            d[a[1]]["狀態"] = "完成"; d[a[1]]["結束"] = time.time(); _save_subs(d)
        print("完成", a[1])
    else:
        print(json.dumps(collect(None), ensure_ascii=False, indent=1)[:3000])
