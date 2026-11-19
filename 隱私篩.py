r"""隱私篩：主討論內容送免費模型前，先在本機篩一次。
使用者 11-20 03:00：「有隱私的你們再幫我篩選，雖然我說的話可能像智慧金庫，但暫時不太需要怕被他們那種切碎訓練搶走」
→ 主討論的一般內容可以送免費小 AI；**個資、金鑰、學生資料、未公開研究結果**留在本機；拿不準的也留本機（Codex 03:01 規格）。

check(text) → (可送?, 處理後文字, 原因)
  - 金鑰樣式（sk-、gsk_、AIza、長十六進位／base64、?k=）→ 遮掉後仍可送
  - 電子郵件、電話 → 遮掉後仍可送
  - 學生資料（導師助手、學生、座號、成績、家長）→ 整則不送
  - 未公開研究結果（研究數字：x/y 全對、正確率、窮舉、單元數、權重數、loss）→ 整則不送
  - 拿不準：太長（> 4000 字）→ 不送
"""
import re

MASK = [
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,}|gsk_[A-Za-z0-9]{12,}|AIza[0-9A-Za-z_\-]{20,}|nvapi-[A-Za-z0-9_\-]{12,})"), "〔金鑰〕"),
    (re.compile(r"[?&]k=[A-Za-z0-9_\-]{8,}"), "?k=〔遮〕"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "〔長碼〕"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "〔信箱〕"),
    (re.compile(r"(?<!\d)(09\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]?\d{6,8})(?!\d)"), "〔電話〕"),
]
BLOCK = [
    ("學生資料", re.compile(r"導師助手|學生資料|座號|成績單|家長|班級名單")),
    ("未公開研究結果", re.compile(r"\d[\d,]*\s*/\s*\d[\d,]*\s*(全對|正確)|正確率\s*\d|窮舉|\d+\s*(個)?單元|\d[\d,]*\s*權重|loss\s*[=＝:]?\s*\d", re.I)),
]


def check(text, max_len=4000):
    """max_len：一般 4000 字；小AI上下文.py 由程式組好、每塊都有出處的上下文可放寬到該家預算（11-20 06:11 次數型給多一點）。"""
    t = text or ""
    if len(t) > max_len:
        return False, "", "太長，拿不準，留本機"
    for why, rx in BLOCK:
        if rx.search(t):
            return False, "", f"含{why}，留本機"
    for rx, rep in MASK:
        t = rx.sub(rep, t)
    return True, t, "可送（已遮金鑰／信箱／電話）" if t != text else "可送"


if __name__ == "__main__":
    for s in ["報錯 手機版待討論頁按鈕被切掉", "鑰匙 sk-abcdefghijklmnop1234 壞了", "word_ctrl 604,288/604,288 全對", "座號 12 的成績"]:
        print(check(s))
