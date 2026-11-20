r"""縮寫介紹（使用者 11-21 06:13：常用縮寫建立成檔案附解說；訊息框左邊「縮寫」按鈕，預設不展開，
展開列出這則訊息命中的縮寫；每個可按「隱藏」；「顯示已隱藏」打勾預設不勾）。

- 縮寫表：H:\Agent共用\縮寫表.json（大家都能補；格式 {"縮寫": "解說"}；也可寫 {"縮寫": {"解說": "...", "別名": ["..."]}}）
- 隱藏清單：logs\縮寫隱藏.json（使用者按的，存中樞，換瀏覽器也在）
GET /api/abbr → {"terms": {縮寫: 解說}, "hidden": [...]}；POST /api/abbr {"op": "hide"|"unhide", "term": ...}
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TERMS = r"H:\Agent共用\縮寫表.json"
HIDDEN = os.path.join(HERE, "logs", "縮寫隱藏.json")


def terms():
    try:
        d = json.load(open(TERMS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out[k] = str(v.get("解說", ""))
            for a in v.get("別名", []) or []:
                out[a] = str(v.get("解說", ""))
        else:
            out[k] = str(v)
    return out


def hidden():
    try:
        return list(json.load(open(HIDDEN, encoding="utf-8")))
    except (OSError, ValueError):
        return []


def set_hidden(term, on):
    h = set(hidden())
    (h.add if on else h.discard)(str(term)[:60])
    os.makedirs(os.path.dirname(HIDDEN), exist_ok=True)
    json.dump(sorted(h), open(HIDDEN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return sorted(h)


WIKI = r"H:\Agent共用\知識庫"
_wiki_cache = {"sig": None, "map": {}}


def wiki_map(ts):
    """使用者 11-21 06:27：縮寫有知識庫詞條就附連結。找法：①檔名含縮寫 ②標題行含縮寫 ③內文含縮寫（英數縮寫要整詞）。
    回 {縮寫: 檔名}；跳過對話紀錄註解這類非詞條檔。依檔案修改時間快取。"""
    import re
    try:
        fs = sorted(f for f in os.listdir(WIKI) if f.endswith(".md") and not f.startswith(("對話紀錄註解", "README")))
        sig = tuple((f, os.path.getmtime(os.path.join(WIKI, f))) for f in fs) + (len(ts),)
    except OSError:
        return {}
    if sig == _wiki_cache["sig"]:
        return _wiki_cache["map"]
    texts = {}
    for f in fs:
        try:
            texts[f] = open(os.path.join(WIKI, f), encoding="utf-8").read()
        except OSError:
            pass
    out = {}
    for t in ts:
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])") if t.isascii() else re.compile(re.escape(t))
        best = None
        for f, s in texts.items():
            if t in f[:-3]:
                best = (0, f); break
            heads = "\n".join(l for l in s.splitlines() if l.startswith("#"))
            rank = 1 if pat.search(heads) else (2 if pat.search(s) else None)
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, f)
        if best:
            out[t] = best[1]
    _wiki_cache.update(sig=sig, map=out)
    return out


def payload():
    ts = terms()
    w = dict(wiki_map(list(ts)))
    try:   # 縮寫表裡寫明 {"詞條": "某.md"} 的，以它為準
        for k, v in json.load(open(TERMS, encoding="utf-8")).items():
            if isinstance(v, dict) and v.get("詞條"):
                for name in [k] + list(v.get("別名", []) or []):
                    w[name] = v["詞條"]
    except (OSError, ValueError):
        pass
    return {"terms": ts, "hidden": hidden(), "wiki": w}
