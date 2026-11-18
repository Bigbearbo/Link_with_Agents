"""存 OpenRouter 鑰匙（使用者自己在視窗貼上；不顯示、不寫進任何紀錄，DPAPI 加密只有這個 Windows 帳號解得開）。
使用者 11-18 21:05：「舊的那把給 DeepSeek，新的是 0 元 key 給輝達，幫我開一個存 key 的」

  雙擊 存key.cmd → 選 1（輝達，$0 鑰匙）或 2（DeepSeek，付費鑰匙）→ 貼上鑰匙 → Enter
存好會用這把鑰匙問 OpenRouter 一次「這把的額度上限」，確認存對了（只顯示上限和已用，不顯示鑰匙）。
"""
import getpass, json, os, shutil, sys, time, urllib.request

WORKER_DIR = r"H:\Agent共用\ai-call\tools\openrouter-worker"
sys.path.insert(0, WORKER_DIR)
from credentials import save_key, load_key, validate_key  # noqa: E402
from host_paths import KEY_FILE  # noqa: E402

TARGETS = {
    "1": ("輝達（免費，$0 鑰匙；Codex 的 worker 用）", str(KEY_FILE), 0),
    "2": ("DeepSeek（付費鑰匙；ds.py 用）", r"H:\Claude用\三方討論\secrets\openrouter-deepseek.dpapi", None),
}


def check(key):
    r = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key})
    return json.load(urllib.request.urlopen(r, timeout=30)).get("data", {})


def main():
    print("要存哪一把？")
    for k, (name, _, _) in TARGETS.items():
        print(f"  {k}. {name}")
    c = input("輸入 1 或 2：").strip()
    if c not in TARGETS:
        return print("沒有這個選項，結束。")
    name, path, want_limit = TARGETS[c]
    key = getpass.getpass("貼上鑰匙（畫面不會顯示），按 Enter：").strip()
    try:
        key = validate_key(key)
        info = check(key)
    except Exception as e:
        return print(f"鑰匙有問題，沒有存：{type(e).__name__}：{str(e).replace(key, '[鑰匙]')[:200]}")
    limit = info.get("limit")
    print(f"OpenRouter 回報：這把的上限 {limit}、已用 {info.get('usage')}")
    if want_limit == 0 and limit not in (0, 0.0):
        if input(f"⚠ 輝達應該用上限 $0 的鑰匙，這把上限是 {limit}。仍要存嗎？(y/N)：").strip().lower() != "y":
            return print("沒有存。")
    if os.path.exists(path):
        bak = path.replace(".dpapi", time.strftime(".備份-%Y%m%d-%H%M%S.dpapi"))
        shutil.copy2(path, bak)
        print(f"舊的已備份：{bak}")
    save_key(key, path)
    ok = load_key(path) == key
    print(f"已存好：{name}" if ok else "存完讀回對不上，請叫 Claude 看看")


if __name__ == "__main__":
    try:
        main()
    finally:
        input("\n按 Enter 關閉…")
