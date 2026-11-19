import os, sys
d = sys.argv[1]
bs = chr(92)
# 回歸測試（Codex 02:14）：同一個控制字元，在 r"..." 裡可以修，在一般 "..." 裡要拒修
open(os.path.join(d, "t_raw.py"), "w", encoding="utf-8").write(
    'p = r"H:' + bs + 'Agent' + chr(7) + 'bc"\n' + 'q = r"D:' + chr(8) + 'in"\n')
open(os.path.join(d, "t_plain.py"), "w", encoding="utf-8").write('p = "H:' + bs + 'Agent' + chr(7) + 'bc"\n')
