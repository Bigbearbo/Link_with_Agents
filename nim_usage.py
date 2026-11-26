r"""輝達（NVIDIA NIM，build.nvidia.com）用量（Claude 11-27；使用者 04:29「都是輝達不用留，我有OPENROUTER次數就夠了，
還有其他免費模型，輝達官網的給你們用，你更新一下顯示和刷新機制」）。

規則（NVIDIA 論壇版主 2026-04／05）：免費、沒有每日額度、不會每天刷新；只有每分鐘次數限制（預設約 40 RPM，依模型與流量浮動）。
所以這裡不算「今天剩幾次」，改算：近 60 秒幾次（對 40）、今天累計（給人看、給總指揮看閒不閒）。
資料來源：免費池帳本 H:\Agent共用\ai-call\免費AI池-用量.jsonl 裡 服務=nvidia 的行（所有走 免費AI池.py 的呼叫都會記）。
寫 H:\Agent共用\輝達-用量.json（舊檔名沿用，欄位換成 RPM 制）。
"""
import datetime
import json
import os
import time

LEDGER = r"H:\Agent共用\ai-call\免費AI池-用量.jsonl"
OUT = r"H:\Agent共用\輝達-用量.json"
RPM_LIMIT = 40          # 官方預設（不保證）
RPM_USE = 40            # 使用者 04:29：不用留給使用者，全部給團隊
DAILY_TARGET = 3000     # 不是上限：總指揮用來判斷「今天用得少、該派工」（平均約 2 次／分鐘）


def _rows(since):
    out = []
    try:
        with open(LEDGER, encoding="utf-8", errors="replace") as f:
            f.seek(max(0, os.path.getsize(LEDGER) - 4_000_000))   # 只看檔尾（帳本會一直長）
            for ln in f:
                if '"nvidia"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("服務") != "nvidia":
                    continue
                try:
                    t = datetime.datetime.strptime(r["t"], "%Y-%m-%d %H:%M:%S").timestamp()
                except (KeyError, ValueError):
                    continue
                if t >= since:
                    out.append(t)
    except OSError:
        pass
    return out


def usage(write=True):
    now = time.time()
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    ts = _rows(min(midnight, now - 60))
    last60 = sum(1 for t in ts if t >= now - 60)
    today = sum(1 for t in ts if t >= midnight)
    rec = {"更新": time.strftime("%Y-%m-%d %H:%M"), "制度": "每分鐘限制（沒有每日額度、不會每天刷新）",
           "近1分鐘": last60, "每分鐘上限": RPM_USE, "今天呼叫": today, "每日目標": DAILY_TARGET,
           "比例": round(today / DAILY_TARGET, 3),   # 給 總指揮派工 的免費算力提醒用（<30% 就催大家用）
           "說明": "輝達＝NVIDIA NIM（build.nvidia.com），全部給團隊用（使用者 11-27 04:29）。使用者自己用 OpenRouter。"
                   "每分鐘最多 40 次；今天呼叫只是統計，沒有上限。"}
    if write:
        try:
            json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except OSError:
            pass
    return rec


def wait_slot(max_wait=120):
    """呼叫前排隊：近 60 秒已達 RPM_USE 就等。回 True＝可以呼叫。"""
    end = time.time() + max_wait
    while time.time() < end:
        if usage(write=False)["近1分鐘"] < RPM_USE:
            return True
        time.sleep(2)
    return False


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(usage(), ensure_ascii=False, indent=1))
