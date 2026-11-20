r"""中樞可調參數（使用者 11-21 01:35：「一些參數可以放到本房或設定那裡，那些頁面也顯示給你們，用程式排版成給你們看的輸出」）。

一份 中樞參數.json 存值；中樞每次用到時讀（改檔就生效，不用重開）。
- 網頁「設定」頁有一張表顯示（給人看）。
- python 中樞參數.py --ai → 寫 H:\Agent共用\中樞參數.md（給 AI 看：值、意思、誰定的、何時）。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "中樞參數.json")
AI_OUT = r"H:\Agent共用\中樞參數.md"

DEFAULTS = {
    "接力換棒秒數": {"值": 60, "單位": "秒", "說明": "接力時某一棒超過這麼久還沒講完，下一棒先開始；兩棒都卡住才再往下",
                "來源": "使用者 11-21 01:23"},
    "AI點名接力上限": {"值": 4, "單位": "次", "說明": "每則使用者訊息後，AI 之間用 @名字 互相點名最多接力幾次",
                  "來源": "既有（MAX_HOPS）"},
    "Codex額度保護": {"值": 0.95, "單位": "週額度比例", "說明": "Codex 週額度用到這個比例以上，群聊名單先不叫它（@Codex 照叫）",
                   "來源": "使用者 11-20 11:51；11-20 16:10 說之後可改線性公式"},
}

_cache = {"mtime": None, "data": {}}


def load():
    try:
        mt = os.path.getmtime(PATH)
        if mt != _cache["mtime"]:
            _cache["data"] = json.load(open(PATH, encoding="utf-8"))
            _cache["mtime"] = mt
        data = _cache["data"]
    except (OSError, ValueError):
        data = {}
    out = {}
    for k, d in DEFAULTS.items():
        v = dict(d)
        if isinstance(data.get(k), dict) and "值" in data[k]:
            v.update(data[k])
        out[k] = v
    return out


def get(name):
    return load()[name]["值"]


def ensure_file():
    if not os.path.exists(PATH):
        json.dump({k: dict(v, 更新=time.strftime("%m-%d %H:%M")) for k, v in DEFAULTS.items()},
                  open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def ai_text():
    L = [f"# 中樞參數（給 AI 讀；{time.strftime('%m-%d %H:%M')} 產生；網頁版＝設定頁；值存在 H:\\Claude用\\三方討論\\中樞參數.json，改檔即生效）", ""]
    for k, v in load().items():
        L.append(f"- **{k}** ＝ {v['值']} {v.get('單位', '')}：{v.get('說明', '')}（{v.get('來源', '')}；更新 {v.get('更新', '—')}）")
    L += ["", "有意見：在討論房提，寫明想改哪個、改成多少、理由；使用者拍板後由 Claude 改。"]
    return "\n".join(L) + "\n"


def html_rows(esc):
    return "".join(f"<tr><td><b>{esc(k)}</b></td><td>{esc(str(v['值']))} {esc(v.get('單位', ''))}</td>"
                   f"<td>{esc(v.get('說明', ''))}</td><td class='meta'>{esc(v.get('來源', ''))}</td></tr>"
                   for k, v in load().items())


if __name__ == "__main__":
    import sys
    ensure_file()
    if "--ai" in sys.argv:
        t = ai_text()
        open(AI_OUT, "w", encoding="utf-8", newline="\n").write(t)
        sys.stdout.reconfigure(encoding="utf-8")
        print(t)
