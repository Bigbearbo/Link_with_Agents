r"""輝達（OpenCode 1.19.5）權限清單：備份＋重開後補回（使用者 11-21 03:20「幫我備份他的權限清單，看重啟之後能不能頂上」）。

背景：Codex 03:18 查 1.19.5 原始碼——視窗裡按「一律允許」只存在記憶體，重開就清空。
做法：把輝達日常工作常被問的權限整理成一張清單（來源＝中樞 🔐 權限通知的歷史紀錄），寫死進 opencode.json。
- 清單本身另存 H:\免費模型用\opencode\權限清單備份.json（給人看、給下次比對）。
- 紅線不放：H:\Claude用\三方討論、H:\金鑰 一律 deny；不加 "cmd /c *" 這種什麼都能跑的萬用規則。
用法（使用者執行；會先自動備份設定檔）：
  python 輝達權限_備份與補回.py            → 套用到 H:\免費模型用\opencode\opencode.json
  python 輝達權限_備份與補回.py <複本路徑>  → 只改複本（測試用）
跑完重開輝達視窗生效。之後又常被問的新權限，加進下面 RULES 再跑一次。
"""
import json, os, shutil, sys, time

CONFIG = r"H:\免費模型用\opencode\opencode.json"
LIST_OUT = r"H:\免費模型用\opencode\權限清單備份.json"

RULES = {
    "external_directory": {
        "H:\\Agent共用\\*": "allow",            # 共用區（03:03 被問）
        "D:\\GPT-Cache\\*": "allow",            # 它用的 python 執行檔所在
        "H:\\Claude用\\三方討論\\*": "deny",     # 中樞程式與原始紀錄：紅線
        "H:\\金鑰\\*": "deny",                  # 鑰匙：紅線
    },
    "edit": {
        "免費模型用/群組/發送/*": "allow",        # 03:18 寫 回覆.json 被問（路徑帶了 免費模型用 前綴）
        "免費模型用\\群組\\發送\\*": "allow",
        "H:/免費模型用/群組/**": "allow",
        "H:\\免費模型用\\群組\\**": "allow",
    },
    "bash": {
        "PY *": "allow",                        # 群組.py 看 main（02:57、02:59 被問）
        "set PYTHONIOENCODING=utf-8*": "allow",
        "ls *": "allow",                        # 03:18 ls 待回 被問；只是列目錄
        "cmd /c \"set PYTHONIOENCODING=utf-8 && python *": "allow",
    },
}


def apply(path):
    if path == CONFIG:
        shutil.copy(path, path + ".bak-" + time.strftime("%Y%m%d-%H%M"))
    d = json.load(open(path, encoding="utf-8-sig"))
    pm = d.setdefault("permission", {})
    added = []
    for kind, rules in RULES.items():
        cur = pm.get(kind)
        if not isinstance(cur, dict):
            cur = {"*": cur} if isinstance(cur, str) else {}
            pm[kind] = cur
        for pat, eff in rules.items():
            if cur.get(pat) != eff:
                cur[pat] = eff
                added.append(f"{kind}: {pat} → {eff}")
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return added


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    target = sys.argv[1] if len(sys.argv) > 1 else CONFIG
    print("改的檔：", target)
    added = apply(target)
    if target == CONFIG:
        json.dump({"更新": time.strftime("%Y-%m-%d %H:%M"), "說明": "輝達日常權限清單；重開後由 opencode.json 頂上",
                   "規則": RULES}, open(LIST_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("清單備份：", LIST_OUT)
    print("新增／改動", len(added), "條：")
    for a in added:
        print("  ", a)
    print("完成；重開輝達視窗生效。")
