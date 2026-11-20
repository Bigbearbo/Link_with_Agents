"""給使用者執行：輝達 OpenCode 設定加「共用區可讀」＋「PY 指令免問」（Claude 11-21 03:16 擬，已在複本測過）。用法：python 輝達權限_共用區可讀.py"""
import json, shutil, sys, time
p = sys.argv[1] if len(sys.argv) > 1 else r"H:\免費模型用\opencode\opencode.json"
shutil.copy(p, p + ".bak-" + time.strftime("%Y%m%d-%H%M"))
d = json.load(open(p, encoding="utf-8-sig"))
pm = d["permission"]
ed = pm.setdefault("external_directory", {})
ed["H:\Agent共用\*"] = "allow"
pm["bash"]["PY *"] = "allow"
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("OK", p, pm["external_directory"])
