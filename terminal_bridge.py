"""讓討論房裡的 Claude 就是「終端機那個 Claude Code」。

中樞端（agents.ClaudeAgent 會呼叫）：
    ask_terminal(room_name, body, should_stop) -> 回覆文字；終端機不在或逾時則回傳 None
終端機端（Claude Code 用 Monitor 執行）：
    python terminal_bridge.py watch      持續寫心跳，每有新請求就印一行通知
    python terminal_bridge.py show <id>  印出請求內容
    （回覆：把文字寫進 terminal/outbox/<id>.txt）
"""
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "terminal")
INBOX, OUTBOX = os.path.join(BASE, "inbox"), os.path.join(BASE, "outbox")
HEARTBEAT = os.path.join(BASE, "heartbeat.txt")
ALIVE_SECONDS = 90         # 心跳多久內算「終端機在線」
REPLY_TIMEOUT = 20 * 60    # 等終端機回覆最多多久，逾時改用備援 Claude
# 上一次已經逾時、終端機還沒回過任何一則：下一輪只等短一點就交備援。
# 11-20 04:42–05:45 終端機程序在但沒人接，連續三輪各空等 20 分鐘（流程優化員 11-20）。
RETRY_TIMEOUT = 3 * 60
_stalled = set()   # 逾時沒回的請求 id；終端機回了任何一則（含遲到的）就清掉


def _timeout_now():
    if not _stalled:
        return REPLY_TIMEOUT
    if any(os.path.exists(os.path.join(OUTBOX, r + ".txt")) for r in _stalled):  # 遲到的回覆：終端機回來了
        _stalled.clear()
        return REPLY_TIMEOUT
    return RETRY_TIMEOUT


def _ensure():
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(OUTBOX, exist_ok=True)


def terminal_alive():
    try:
        return time.time() - os.path.getmtime(HEARTBEAT) < ALIVE_SECONDS
    except OSError:
        return False


# 收件監看的心跳（使用者 11-23 00:0x：「監看怎麼常常掛掉？設計個自動偵測＋報錯」）。
# 終端機 Claude 的 Monitor 每 3 秒寫一次；Monitor 每輪最多 30 分，到期到重掛之間會有空檔。
# 監看停了超過 MONITOR_SECONDS：這一輪只等 MONITOR_WAIT 就交備援（不空等 20 分），並記一筆斷線紀錄給常駐檢查／巡檢。
MONITOR_HB = os.path.join(BASE, "monitor_heartbeat.txt")
MONITOR_SECONDS = 120
MONITOR_WAIT = 3 * 60
MONITOR_LOG = os.path.join(HERE, "logs", "監看斷線.log")


def monitor_alive():
    try:
        return time.time() - os.path.getmtime(MONITOR_HB) < MONITOR_SECONDS
    except OSError:
        return False


def _log_monitor_gap(rid, room_name):
    try:
        with open(MONITOR_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')}\t{rid}\t{room_name}\t監看心跳停了，這輪最多等 {MONITOR_WAIT // 60} 分\n")
    except OSError:
        pass


def ask_terminal(room_name, body, should_stop, on_wait=None):
    """把請求交給終端機的 Claude，等它回覆。回傳文字；不在線、逾時或被停止回傳 None。"""
    if not terminal_alive():
        return None
    _ensure()
    rid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:6]
    req = {"id": rid, "room": room_name, "time": time.strftime("%m-%d %H:%M:%S"), "body": body}
    tmp = os.path.join(INBOX, rid + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(INBOX, rid + ".json"))
    out = os.path.join(OUTBOX, rid + ".txt")
    t0 = time.time()
    limit = _timeout_now()
    if not monitor_alive():
        limit = min(limit, MONITOR_WAIT)
        _log_monitor_gap(rid, room_name)
    try:
        while time.time() - t0 < limit:
            if should_stop():
                return None
            if os.path.exists(out):
                time.sleep(0.3)  # 讓寫入完成
                with open(out, encoding="utf-8") as f:
                    text = f.read().strip()
                os.replace(out, out + ".done")
                _stalled.clear()
                return text or "（終端機 Claude 回了空白）"
            if on_wait:
                on_wait(int(time.time() - t0))
            if not terminal_alive() and time.time() - t0 > 30:
                return None  # 終端機中途離線
            time.sleep(1)
        _stalled.add(rid)

        return None
    finally:
        try:
            os.replace(os.path.join(INBOX, rid + ".json"), os.path.join(INBOX, rid + ".handled"))
        except OSError:
            pass


def watch():
    _ensure()
    announced = set()
    print("三方討論終端機橋接：開始監看", flush=True)
    while True:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        for name in sorted(os.listdir(INBOX)):
            if name.endswith(".json") and name not in announced:
                announced.add(name)
                try:
                    with open(os.path.join(INBOX, name), encoding="utf-8") as f:
                        req = json.load(f)
                    print(f"【三方討論】「{req['room']}」輪到 Claude 回覆 id={req['id']}", flush=True)
                except Exception:
                    pass
        time.sleep(5)


def heartbeat(pid):
    """獨立的心跳：只要終端機那個 Claude Code（pid）還開著就持續寫心跳。
    不跟 Monitor 綁在一起，Claude 忙著做別的事或 Monitor 重開的空檔，房間也不會誤判它離線而叫備援。"""
    import ctypes
    _ensure()
    k32 = ctypes.windll.kernel32

    def alive():
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE

    while alive():
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" pid={pid}")
        time.sleep(20)


def show(rid):
    with open(os.path.join(INBOX, rid + ".json"), encoding="utf-8") as f:
        req = json.load(f)
    print(f"房間：{req['room']}　時間：{req['time']}　id：{req['id']}\n")
    print(req["body"])
    print(f"\n→ 回覆寫到：{os.path.join(OUTBOX, rid + '.txt')}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["watch"]:
        watch()
    elif sys.argv[1:2] == ["heartbeat"] and len(sys.argv) > 2:
        heartbeat(sys.argv[2])
    elif sys.argv[1:2] == ["show"] and len(sys.argv) > 2:
        show(sys.argv[2])
    else:
        print(__doc__)
