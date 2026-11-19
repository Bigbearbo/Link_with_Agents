r"""派報規則候選：從小 AI 的路由紀錄找「一直這樣判、也沒被糾正」的情況，提成固定規則候選。
使用者 11-20 02:51：「程式沒有，小 AI 判對，也可以看能不能把規則寫進程式，長期下來程式越包含越多，小 AI 逐漸被程式取代，解放生產力」

做法：
  - 讀 logs\派報\路由紀錄.jsonl 裡「誰決定＝小AI」的紀錄，以（給誰, 候選消息的來源類型, 關鍵字）分組。
  - 同一組至少 N 次（預設 4）、判斷全部一致、而且沒有任何一筆被標「判對: false」→ 列為候選。
  - **不自動寫進規則**：印出候選，由 Claude／Codex 看過再用「加」寫進 派報規則.json（附來源）。寫進去後拿舊紀錄重跑，確認規則跟小 AI 過去的判斷一致。
  - 另外算「程式接手率」：最近 7 天有幾成路由是規則決定的——越高代表小 AI 越被程式取代。

用法：
  PY 派報規則候選.py            列候選＋接手率
  PY 派報規則候選.py 加 <編號>   把第 N 個候選寫進 派報規則.json，並用舊紀錄驗證
  PY 派報規則候選.py 錯 <時間> <給誰>   標記某次判錯（判對=false），之後不會被提成規則
"""
import json, os, sys, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "派報", "路由紀錄.jsonl")
RULES = os.path.join(HERE, "派報規則.json")
MIN_N = 4


def load():
    out = []
    try:
        for line in open(LOG, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def kind(h):
    return "群組" if h.startswith("機器人群") else "變動"


def candidates(recs):
    groups = defaultdict(list)
    for r in recs:
        if r.get("誰決定") != "小AI":
            continue
        kinds = tuple(sorted({kind(h) for h in r.get("候選", [])}))
        groups[(r["給"], kinds, r.get("關鍵字", ""))].append(r)
    out = []
    for (who, kinds, kw), rs in groups.items():
        if len(rs) < MIN_N or any(r.get("判對") is False for r in rs):
            continue
        dec = {r["叫醒"] for r in rs}
        if len(dec) == 1:
            out.append({"給": who, "含": [kw], "叫醒": dec.pop(), "次數": len(rs), "來源類型": list(kinds),
                        "來源": f"小 AI 連續 {len(rs)} 次同判、未被糾正（{rs[0]['時間']}～{rs[-1]['時間']}）"})
    return out


def takeover(recs, days=7):
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    rs = [r for r in recs if r.get("時間", "") >= cut]
    return (sum(r.get("誰決定") == "規則" for r in rs), len(rs))


if __name__ == "__main__":
    recs = load()
    a = sys.argv[1:]
    if a[:1] == ["加"]:
        c = candidates(recs)[int(a[1]) - 1]
        rules = json.load(open(RULES, encoding="utf-8")) if os.path.exists(RULES) else []
        rules.append({k: c[k] for k in ("給", "含", "叫醒", "來源")})
        json.dump(rules, open(RULES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        same = sum(1 for r in recs if r["給"] == c["給"] and c["含"][0] == r.get("關鍵字") and r["叫醒"] == c["叫醒"])
        print(f"已加規則；舊紀錄驗證：{same} 筆跟規則一致")
    elif a[:1] == ["錯"]:
        for r in recs:
            if r["時間"].startswith(a[1]) and r["給"] == a[2]:
                r["判對"] = False
        with open(LOG, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
        print("已標記判錯")
    else:
        n, tot = takeover(recs)
        print(f"程式接手率（7 天）：{n}/{tot}" + (f"＝{100 * n / tot:.0f}%" if tot else ""))
        for i, c in enumerate(candidates(recs), 1):
            print(f"{i}. 給 {c['給']}｜含「{c['含'][0]}」｜{'叫醒' if c['叫醒'] else '放下一期'}｜{c['來源']}")
        if not candidates(recs):
            print(f"（還沒有候選：同一情況要小 AI 一致判 {MIN_N} 次以上）")
