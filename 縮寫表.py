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


def payload():
    return {"terms": terms(), "hidden": hidden()}
