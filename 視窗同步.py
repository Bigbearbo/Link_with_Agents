r"""視窗同步：使用者在各 AI 視窗直接講的話（＋那位 AI 回覆的摘要），旁聽貼進中樞「主討論」當上文。
使用者 11-23 18:0x：「把我跟大家視窗說的話也同步到主討論，不用喚醒，有上文之後能一起看。」
18:1x：終端機 Claude 最重要；回覆摘要叫免費小 AI 寫，失敗退回截前 200 字；記帳；間隔做成團隊參數。

  python 視窗同步.py              跑一次（中樞 window_sync_loop 照團隊參數「視窗同步間隔秒」定時叫）
  python 視窗同步.py 乾跑          只印出會貼什麼，不貼、不動讀取位置（摘要照叫、照記帳）
  python 視窗同步.py 最近 N [來源]  補貼最近 N 筆（來源：claude／codex／輝達／gemini，預設 claude）
  python 視窗同步.py 統計          每天摘要幾次、失敗幾次、用哪個模型
  加 --room <房間id> 可改貼到測試房；加 --不摘要 只截前 200 字

貼法：POST /api/listen → 中樞存成 kind=silent、src=旁聽，作者「使用者（在 X 視窗）」，所有自動輪流／點名／喚醒都跳過，
AI 下次被叫起來時在上文看得到。來源（只讀）：
  終端機 Claude：~/.claude/projects/H--Claude-/*.jsonl（origin.kind=human、非子代理；回覆取該輪最後一段文字，到 turn_duration 為止）
  Codex：~/.codex/sessions/**/*.jsonl（thread_source=user 且不是 codex_exec；task_started 的 turn_trigger 是人打的才算；回覆＝task_complete.last_agent_message）
  輝達：~/.local/share/opencode/opencode.db（唯讀開；目錄含「免費模型用」的主對話；【…】開頭的中樞注入不算）
  Gemini：~/.gemini/antigravity-cli（cache/last_conversations.json 裡 H:\Gemini用 對到的對話 → brain/<id>/.system_generated/logs/transcript.jsonl）
讀取位置存在 logs\視窗同步-狀態.json；摘要帳在 logs\視窗同步_摘要.jsonl。
"""
import glob
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "視窗同步-狀態.json")
LEDGER = os.path.join(HERE, "logs", "視窗同步_摘要.jsonl")
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude", "projects", "H--Claude-")
CODEX_DIR = os.path.join(HOME, ".codex", "sessions")
OPENCODE_DB = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
AGY = os.path.join(HOME, ".gemini", "antigravity-cli")
RECENT = 2 * 86400            # 只看最近 2 天有動的檔
STUCK = 15 * 60               # 一輪做超過 15 分鐘還沒完：先貼使用者的話＋目前為止的回覆
QUIET = 60                    # 沒有明確結束記號的來源（輝達、Gemini）：最後回覆後安靜 60 秒才算這輪完
USER_MAX, SHORT = 1500, 200
CODEX_HUMAN = {"composer", "remote_ios", "queue", "user", "send_user_message_async_question"}
SUMMARIZERS = [("groq", None), ("nvidia", None)]   # 免費AI池：Groq（gpt-oss-120b，每天約 1000 次）→ NVIDIA NIM 備援


# ---------- 共用 ----------
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def iso_local(ts):
    """2026-11-23T10:15:44.283Z → (epoch 秒, '11-23 18:15')"""
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (TypeError, ValueError):
        t = time.time()
    return t, time.strftime("%m-%d %H:%M", time.localtime(t))


def read_lines(path, off):
    """從 off 讀到最後一個完整行；回傳 [(行起點, dict)], 新位置。"""
    out = []
    with open(path, "rb") as fh:
        fh.seek(off)
        pos = off
        for ln in fh:
            if not ln.endswith(b"\n"):
                break
            try:
                out.append((pos, json.loads(ln)))
            except ValueError:
                pass
            pos += len(ln)
    return out, pos


def recent_files(pattern):
    cut = time.time() - RECENT
    return sorted((f for f in glob.glob(pattern, recursive=True) if os.path.getmtime(f) >= cut), key=os.path.getmtime)


def start_off(path, cur, backfill):
    size = os.path.getsize(path)
    if backfill:
        return max(0, size - 6_000_000)
    if cur is None:
        return size              # 第一次看到：從現在開始（不倒灌舊對話）
    return 0 if cur > size else cur


def clean(t):
    t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t or "", flags=re.S)
    return t.strip()


# ---------- 終端機 Claude ----------
def src_claude(st, backfill=False):
    cur_all, items = st.setdefault("claude", {}), []
    for f in recent_files(os.path.join(CLAUDE_DIR, "*.jsonl")):
        off = start_off(f, cur_all.get(f), backfill)
        rows, end = read_lines(f, off)
        pend, new_cur = None, end

        def done(p, note=""):
            reply = clean(p["reply"])
            items.append({"key": "claude:" + p["uuid"], "window": "終端機 Claude", "ai": "Claude", "t": p["t"], "time": p["time"],
                          "user": p["user"], "reply": (note + reply) if reply or note else ""})

        for pos, d in rows:
            if d.get("isSidechain"):
                continue
            ty = d.get("type")
            if ty == "user" and (d.get("origin") or {}).get("kind") == "human":
                c = d.get("message", {}).get("content")
                text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if x.get("type") == "text")
                text = clean(text)
                if not text or text.startswith(("<command-", "<local-command")):
                    continue
                if pend:
                    done(pend)
                t, hm = iso_local(d.get("timestamp"))
                pend = {"uuid": d.get("uuid") or str(pos), "t": t, "time": hm, "user": text, "reply": "", "pos": pos}
            elif ty == "assistant" and pend:
                txt = "\n".join(x.get("text", "") for x in d.get("message", {}).get("content") or [] if x.get("type") == "text").strip()
                if txt:
                    pend["reply"] = txt
            elif ty == "system" and d.get("subtype") == "turn_duration" and pend:
                done(pend)
                pend = None
        if pend:
            if time.time() - pend["t"] > STUCK:
                done(pend, "（這輪還在做，先貼目前為止的回覆）")
            else:
                new_cur = pend["pos"]   # 這輪還沒講完：下次從這則使用者訊息重讀
        cur_all[f] = new_cur
    return items


# ---------- Codex ----------
def codex_meta(st, f):
    meta = st.setdefault("codex_meta", {})
    if f not in meta:
        try:
            with open(f, encoding="utf-8") as fh:
                p = json.loads(fh.readline()).get("payload", {})
        except (OSError, ValueError):
            return None
        ok = p.get("thread_source") == "user" and p.get("originator") != "codex_exec"
        cwd = p.get("cwd") or ""
        label = ("有下文 Codex" if "有下文" in cwd else "Codex 終端機" if p.get("originator") == "codex-tui" else "Codex")
        meta[f] = label if ok else ""
    return meta[f]


def src_codex(st, backfill=False):
    cur_all, items = st.setdefault("codex", {}), []
    for f in recent_files(os.path.join(CODEX_DIR, "**", "*.jsonl")):
        label = codex_meta(st, f)
        if not label:
            continue
        off = start_off(f, cur_all.get(f), backfill)
        rows, end = read_lines(f, off)
        pend, new_cur = None, end
        for pos, d in rows:
            p = d.get("payload") or {}
            if d.get("type") == "event_msg" and p.get("type") == "task_started":
                trig = (p.get("turn_attribution") or {}).get("turn_trigger")
                t, hm = iso_local(d.get("timestamp"))
                pend = {"turn": p.get("turn_id"), "t": t, "time": hm, "user": None, "pos": pos} if trig in CODEX_HUMAN else None
            elif pend and d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user" and pend["user"] is None:
                txt = "\n".join(x.get("text", "") for x in p.get("content") or [] if x.get("type") == "input_text"
                                and not x.get("text", "").lstrip().startswith("<")).strip()
                if txt:
                    pend["user"] = txt
            elif pend and d.get("type") == "event_msg" and p.get("type") in ("task_complete", "turn_aborted"):
                if pend["user"]:
                    items.append({"key": f"codex:{pend['turn']}", "window": label, "ai": "Codex", "t": pend["t"], "time": pend["time"],
                                  "user": pend["user"], "reply": p.get("last_agent_message") or "（這輪被中斷）"})
                pend = None
        if pend and pend["user"] and time.time() - pend["t"] <= STUCK:
            new_cur = pend["pos"]
        elif pend and pend["user"]:
            items.append({"key": f"codex:{pend['turn']}", "window": label, "ai": "Codex", "t": pend["t"], "time": pend["time"],
                          "user": pend["user"], "reply": "（這輪還在做）"})
        cur_all[f] = new_cur
    return items


# ---------- 輝達（OpenCode） ----------
def src_nvidia(st, backfill=False):
    if not os.path.exists(OPENCODE_DB):
        return []
    db = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=5)
    try:
        cur = st.get("nvidia")
        if cur is None and not backfill:
            st["nvidia"] = int(time.time() * 1000)
            return []
        since = 0 if backfill else int(cur)
        if backfill:
            since = int((time.time() - RECENT) * 1000)
        sess = [r[0] for r in db.execute("select id from session where parent_id is null and directory like ?", ("%免費模型用%",))]
        items, hold, top = [], None, since
        for sid in sess:
            msgs = db.execute("select id, time_created, data from message where session_id=? and time_created>? order by time_created",
                              (sid, since)).fetchall()
            for i, (mid, tc, data) in enumerate(msgs):
                d = json.loads(data)
                if d.get("role") != "user":
                    continue
                top = max(top, tc)
                parts = [json.loads(x[0]) for x in db.execute("select data from part where message_id=? order by time_created", (mid,))]
                text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text" and not p.get("synthetic")).strip()
                if not text or text.startswith("【"):
                    continue
                reply, last_done, closed = "", None, False
                for mid2, tc2, data2 in msgs[i + 1:]:
                    d2 = json.loads(data2)
                    if d2.get("role") == "user":
                        closed = True
                        break
                    ps = [json.loads(x[0]) for x in db.execute("select data from part where message_id=? order by time_created", (mid2,))]
                    tx = "\n".join(p.get("text", "") for p in ps if p.get("type") == "text").strip()
                    if tx:
                        reply = tx
                    last_done = (d2.get("time") or {}).get("completed")
                age = time.time() - tc / 1000
                if not closed and not (last_done and time.time() - last_done / 1000 > QUIET) and age <= STUCK:
                    hold = tc if hold is None else min(hold, tc)
                    continue
                items.append({"key": f"nvidia:{mid}", "window": "輝達 OpenCode", "ai": "輝達", "t": tc / 1000,
                              "time": time.strftime("%m-%d %H:%M", time.localtime(tc / 1000)), "user": text, "reply": reply})
        if backfill and cur is not None:
            top = max(top, int(cur))
        st["nvidia"] = (hold - 1) if hold is not None else top   # 還沒講完的那則：下次從它重讀（已貼的靠清單去重）
        return items
    finally:
        db.close()


# ---------- Gemini（agy） ----------
def src_gemini(st, backfill=False):
    try:
        last = json.load(open(os.path.join(AGY, "cache", "last_conversations.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    convs = {v for k, v in last.items() if k.lower().rstrip("\\").startswith(r"h:\gemini用")}
    cur_all, items = st.setdefault("gemini", {}), []
    for cid in convs:
        f = os.path.join(AGY, "brain", cid, ".system_generated", "logs", "transcript.jsonl")
        if not os.path.exists(f):
            continue
        rows, _ = read_lines(f, 0)
        seen = cur_all.get(cid)
        if seen is None and not backfill:
            cur_all[cid] = max([-1] + [int(d.get("step_index", -1)) for _, d in rows])
            continue
        seen = -1 if backfill else int(seen)
        pend, hold, top = None, None, seen

        def done(p):
            items.append({"key": f"gemini:{cid}:{p['step']}", "window": "Gemini agy", "ai": "Gemini", "t": p["t"], "time": p["time"],
                          "user": p["user"], "reply": p["reply"]})

        for _, d in rows:
            step = int(d.get("step_index", -1))
            if d.get("type") == "USER_INPUT":
                if pend:
                    done(pend)
                    pend = None
                if step <= seen:
                    continue
                m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", d.get("content") or "", re.S)
                text = (m.group(1) if m else d.get("content") or "").strip()
                if d.get("source") != "USER_EXPLICIT" or not text or text.startswith("【"):
                    top = max(top, step)
                    continue
                t, hm = iso_local(d.get("created_at"))
                pend = {"step": step, "t": t, "time": hm, "user": text, "reply": "", "last": t, "final": False}
            elif pend and pend["final"] and pend["reply"] and d.get("type") == "SYSTEM_MESSAGE":
                done(pend)   # 這輪已講完，後面是系統通知或自排程開的新一輪（不是回這句）
                pend = None
            elif pend and d.get("type") == "PLANNER_RESPONSE":
                t, _hm = iso_local(d.get("created_at"))
                pend["last"] = t
                if (d.get("content") or "").strip():
                    pend["reply"] = d["content"].strip()
                pend["final"] = not d.get("tool_calls")
            top = max(top, step) if not pend else top
        if pend:
            if (pend["final"] and time.time() - pend["last"] > QUIET) or time.time() - pend["t"] > STUCK:
                done(pend)
                top = max(top, max(int(d.get("step_index", -1)) for _, d in rows))
            else:
                hold = pend["step"] - 1
        else:
            top = max([top] + [int(d.get("step_index", -1)) for _, d in rows])
        cur_all[cid] = hold if hold is not None else top
    return items


SOURCES = {"claude": src_claude, "codex": src_codex, "輝達": src_nvidia, "gemini": src_gemini}


# ---------- 摘要（免費小 AI；失敗退回截前 200 字） ----------
def _ledger(**kw):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"時間": time.strftime("%Y-%m-%d %H:%M:%S")}, **kw), ensure_ascii=False) + "\n")


def _mask(t):
    try:
        import importlib
        ys = importlib.import_module("隱私篩")
        for rx, rep in ys.MASK:
            t = rx.sub(rep, t)
    except Exception:
        pass
    return t


def summarize(it, use_ai=True):
    reply = (it.get("reply") or "").strip()
    if not reply:
        return "", "無回覆"
    if len(reply) <= SHORT:
        return reply, "原文"
    cut = reply[:SHORT].rstrip() + "…"
    if not use_ai:
        return cut, "截前200字"
    sys.path.insert(0, HERE)
    try:
        import importlib
        ys = importlib.import_module("隱私篩")
        ok, safe, why = ys.check(f"使用者問：{it['user'][:300]}\n\n{it['ai']} 回覆：{reply[:6000]}", max_len=7000)
    except Exception as e:
        ok, safe, why = False, "", f"隱私篩壞了 {type(e).__name__}"
    if not ok:
        _ledger(來源=it["window"], 模型="", 輸入字數=len(reply), 輸出字數=0, 結果="隱私篩擋下：" + why, 秒數=0)
        return cut, "截前200字（隱私篩：" + why + "）"
    prompt = (f"下面是使用者在「{it['window']}」視窗跟 {it['ai']} 的一輪對話。請用繁體中文寫 80～180 字摘要，"
              f"只寫 {it['ai']} 這輪回覆的重點：做了什麼、決定了什麼、還在等誰或等什麼。不要開場白、不要加標題，只輸出摘要。\n\n{safe}")
    try:
        sys.path.insert(0, r"H:\Agent共用\ai-call")
        import importlib
        pool = importlib.import_module("免費AI池")
    except Exception as e:
        _ledger(來源=it["window"], 模型="", 輸入字數=len(prompt), 輸出字數=0, 結果=f"載入失敗 {type(e).__name__}", 秒數=0)
        return cut, "截前200字"
    for name, model in SUMMARIZERS:
        t0 = time.time()
        try:
            out = (pool.ask(name, prompt, model=model, max_tokens=500, timeout=30) or "").strip()
            out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
            ok = 20 <= len(out) <= 600
            _ledger(來源=it["window"], 模型=name + (":" + model if model else ""), 輸入字數=len(prompt), 輸出字數=len(out),
                    結果="成功" if ok else "內容不合用", 秒數=round(time.time() - t0, 1))
            if ok:
                return out, f"小AI·{name}"
        except Exception as e:
            _ledger(來源=it["window"], 模型=name, 輸入字數=len(prompt), 輸出字數=0,
                    結果=f"失敗 {type(e).__name__} {str(e)[:80]}", 秒數=round(time.time() - t0, 1))
    return cut, "截前200字"


def compose(it, use_ai=True):
    user = it["user"] if len(it["user"]) <= USER_MAX else it["user"][:USER_MAX] + "…（後略）"
    summ, how = summarize(it, use_ai)
    text = f"〔旁聽｜{it['time']}｜不用回〕{user}"
    if summ:
        text += f"\n\n↳ {it['ai']} 回覆摘要（{how}）：{summ}"
    return _mask(text)


# ---------- 送中樞 ----------
def api(path, body):
    key = open(os.path.join(r"H:\金鑰", "web-key.txt"), encoding="utf-8").read().strip()
    req = urllib.request.Request("http://127.0.0.1:8765" + path, headers={"X-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def run(names, dry=False, backfill_n=0, room=None, use_ai=True):
    st = load_state()
    sent = st.setdefault("sent", [])
    items = []
    for n in names:
        try:
            items += SOURCES[n](st, backfill=bool(backfill_n))
        except Exception as e:
            print(f"[{n}] 讀取失敗：{type(e).__name__} {e}")
    items = [it for it in sorted(items, key=lambda x: x["t"]) if it["key"] not in sent]
    if backfill_n:
        items = items[-backfill_n:]
    ok_all = True
    for it in items:
        text = compose(it, use_ai)
        if dry:
            print(f"--- 使用者（在 {it['window']} 視窗）{it['time']} ---\n{text}\n")
            continue
        try:
            r = api("/api/listen", {"room": room, "window": it["window"], "text": text, "client_id": "sync:" + it["key"]})
            if not r.get("ok"):
                raise RuntimeError(r)
            sent.append(it["key"])
            print(f"已貼：{it['window']} {it['time']} {it['user'][:30]}")
        except Exception as e:
            ok_all = False
            print(f"貼不上（中樞沒開？）：{type(e).__name__} {e}")
            break
    del sent[:-1000]
    if dry:
        return items
    if ok_all or backfill_n:
        save_state(st)
    else:   # 貼到一半失敗：只存已貼清單，讀取位置不動，下次重讀（已貼的靠清單去重）
        old = load_state()
        old["sent"] = sent
        save_state(old)
    return items


def stats():
    rows = []
    try:
        for ln in open(LEDGER, encoding="utf-8"):
            try:
                rows.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        print("還沒有摘要紀錄")
        return
    days = {}
    for r in rows:
        d = days.setdefault(r["時間"][:10], {"次": 0, "成功": 0, "失敗": 0, "秒": 0.0, "模型": {}, "來源": {}})
        d["次"] += 1
        good = r.get("結果") == "成功"
        d["成功" if good else "失敗"] += 1
        d["秒"] += float(r.get("秒數") or 0)
        d["模型"][r.get("模型") or "—"] = d["模型"].get(r.get("模型") or "—", 0) + 1
        d["來源"][r.get("來源")] = d["來源"].get(r.get("來源"), 0) + 1
    for day, d in sorted(days.items()):
        print(f"{day}：摘要 {d['次']} 次（成功 {d['成功']}、失敗 {d['失敗']}），平均 {d['秒'] / max(1, d['次']):.1f} 秒｜"
              f"模型 {d['模型']}｜來源 {d['來源']}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = sys.argv[1:]
    room = a[a.index("--room") + 1] if "--room" in a else None
    use_ai = "--不摘要" not in a
    dry = "乾跑" in a or "--乾跑" in a
    a = [x for i, x in enumerate(a) if x not in ("--room", "--不摘要", "乾跑", "--乾跑") and (i == 0 or a[i - 1] != "--room")]
    alias = {"claude": "claude", "codex": "codex", "輝達": "輝達", "nvidia": "輝達", "gemini": "gemini"}
    if a[:1] == ["統計"]:
        stats()
    elif a[:1] == ["最近"]:
        n = int(a[1]) if len(a) > 1 else 1
        names = [alias.get(x.lower(), x) for x in a[2:]] or ["claude"]
        run(names, dry=dry, backfill_n=n, room=room, use_ai=use_ai)
    else:
        run(list(SOURCES), dry=dry, room=room, use_ai=use_ai)
