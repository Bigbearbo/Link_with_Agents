r"""對話紀錄匯出給外部模型 AI（使用者 11-21 01:51「你處理即可」：輝達要做對話紀錄註解，但不該讀整個中樞資料夾，裡面有 secrets\）。
來源：中樞 logs-按日\<房間>\<日期>.md；每則訊息先過 隱私篩.check：可送→遮掉金鑰／信箱／電話後放；不可送→換成「〔本機保留：原因〕」。
輸出：H:\Agent共用\對話紀錄匯出\<房間>\<日期>.md（只匯出主討論、機器人互相討論群）。中樞每 10 分鐘的巡檢不跑這支，手動或排程跑：
  python 對話紀錄匯出.py
"""
import glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 隱私篩

SRC = os.path.join(HERE, "logs-按日")
DST = r"H:\Agent共用\對話紀錄匯出"
ROOMS = ["主討論", "機器人互相討論群"]


def convert(text):
    parts = re.split(r"(?m)^(?=## )", text)
    out, kept, held = [], 0, 0
    for p in parts:
        if not p.startswith("## "):
            out.append(p); continue
        head, _, body = p.partition("\n")
        ok, cleaned, why = 隱私篩.check(body)
        if ok:
            out.append(head + "\n" + cleaned); kept += 1
        else:
            out.append(f"{head}\n〔本機保留：{why}〕\n\n"); held += 1
    return "".join(out), kept, held


def main():
    tot = [0, 0]
    for room in ROOMS:
        os.makedirs(os.path.join(DST, room), exist_ok=True)
        for f in sorted(glob.glob(os.path.join(SRC, room, "*.md"))):
            t, k, h = convert(open(f, encoding="utf-8").read())
            hdr = f"<!-- 匯出自中樞 logs-按日；已過隱私篩：金鑰／信箱／電話遮掉，學生資料、未公開研究數字等整則留本機（{k} 則匯出、{h} 則保留） -->\n"
            open(os.path.join(DST, room, os.path.basename(f)), "w", encoding="utf-8", newline="\n").write(hdr + t)
            tot[0] += k; tot[1] += h
    open(os.path.join(DST, "README.md"), "w", encoding="utf-8", newline="\n").write(
        "# 對話紀錄匯出（給外部模型的 AI 讀，例：輝達做註解）\n\n"
        "- 來源：中樞 logs-按日；只含主討論、機器人互相討論群。\n"
        "- 已過隱私篩：金鑰／信箱／電話遮掉；學生資料、未公開研究結果等整則換成「〔本機保留：原因〕」。\n"
        "- **請讀這裡，不要讀 H:\Claude用\三方討論\（裡面有 secrets\）。**\n"
        "- 註解請另存（例：H:\Agent共用\對話紀錄匯出\註解\<日期>.md），並標註註解身分（誰、哪個模型、何時）。\n")
    print(f"匯出 {tot[0]} 則、保留 {tot[1]} 則 → {DST}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
