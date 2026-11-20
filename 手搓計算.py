r"""聊天室裡叫手搓網路算題（使用者 11-20 08:15「期待手搓網路能做點小事」；08:23「他真的有用了」→ 加減也接上）。
使用者發言行首寫「手搓 33*3」「手搓 127÷5」「手搓 45+67」「手搓 90-7」（0..127；* × x 乘、/ ÷ 除、+ ＋ 加、- − － 減）
→ 中樞用手搓 v2 加減乘除網路（H:\Agent共用\專案\手搓算術\v2\handfill_v2_addsub.py，權重全手填、沒有訓練）真的跑一遍，
回答案、內部步數、題目／答案的 v2 位元。
主機只貼板子、看交卷位；答案是從網路寫出的作答欄解碼，不是主機算的（另附主機核對）。
"""
import importlib.util, os, re, sys

V2 = r"H:\Agent共用\專案\手搓算術\v2\handfill_v2_addsub.py"
_mod = None
OPS = {"MUL": "*×xX", "DIV": "/÷", "ADD": "+＋", "SUB": "-−－"}
SYM = {"MUL": "×", "DIV": "÷", "ADD": "+", "SUB": "−"}


def _load():
    global _mod
    if _mod is None:
        s = importlib.util.spec_from_file_location("handfill_v2_addsub", V2)
        _mod = importlib.util.module_from_spec(s)
        s.loader.exec_module(_mod)
        _mod._net = _mod.build()
    return _mod


def parse(text):
    m = re.match(r"^\s*手搓\s*(\d{1,5})\s*([*×xX/÷+＋\-−－])\s*(\d{1,5})\s*[=＝?？]?\s*$", text or "")
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    return a, next(k for k, v in OPS.items() if op in v), b


_word = None


def word_answer(a, op, b):
    """詞級版（Fable 起頭、Claude 接手的 word_ctrl v3.1，538 單元）：數字寫成 6 位一段的黏數，網路自己解碼、算、寫回詞級答案。"""
    global _word
    lim = 4095 if op in ("ADD", "SUB") else 255
    if a > lim or b > lim:
        return f"🔧 {a}{SYM[op]}{b}：手搓網路的詞級版加減只到 0～4095、乘除只到 0～255（再大要等 v2 多詞規格接上）。"
    if op == "SUB" and a < b:
        return f"🔧 {a}−{b}：手搓網路目前只會不是負數的減法。"
    if op == "DIV" and b == 0:
        return "🔧 除以 0：手搓網路會拒答。"
    import numpy as np
    if _word is None:
        sys.path.insert(0, r"H:\Agent共用\claude-接手Fable-20261117")
        import word_ctrl as wc
        _word = (wc, wc.build())
    wc, net = _word
    name = {"ADD": "加", "SUB": "減", "MUL": "乘", "DIV": "除"}[op]
    board = np.zeros((1, wc.NIN), np.int64)
    board[:, wc.c("OP", 0)], board[:, wc.c("OP", 1)] = wc.OPS[name]
    board[:, wc.POS["WX"][0]:wc.POS["WY"][0] + 24] = wc.encode([a], [b], False)
    out, steps, done = wc.mc.run(net, board, max_rounds=40)
    got, why = wc.decode(out[0, wc.POS["OUT"][0]:wc.POS["OUT"][0] + 48])
    if why or not got:
        return f"🔧 手搓網路（詞級版）這題沒交出合法答案（{why or '拒答'}）"
    gold = {"ADD": [a + b], "SUB": [a - b], "MUL": [a * b], "DIV": list(divmod(a, b))}[op]
    shown = f"{got[0]} 餘 {got[1]}" if op == "DIV" else f"{got[0]}"
    ok = "✅ 主機核對一致" if got == gold else f"❌ 主機核對不一致（應為 {gold}）"
    return (f"🔧 手搓網路（詞級版，Fable 線）算的：{a} {SYM[op]} {b} = **{shown}**\n{ok}｜內部 {int(steps[0])} 步｜"
            f"{len(net[1])} 個手填門檻單元，沒有訓練｜數字用 6 位一段的黏數詞（超過 v2 的 127，等多詞規格接上後會統一）")


def answer(text):
    q = parse(text)
    if not q:
        return None
    a, op, b = q
    big = a > 127 or b > 127 or (op == "ADD" and a + b > 127)
    if big:   # 使用者 11-20 09:29「怎麼不接 fable 的繼續搓」：超過 v2 範圍就交給詞級版 word_ctrl（黏數，加減 0..4095、乘除 0..255）
        return word_answer(a, op, b)
    if not (0 <= a <= 127 and 0 <= b <= 127):
        return "🔧 手搓網路目前只會 0～127 的加減乘除（v2 格式一個詞 7 位）。"
    if op == "DIV" and b == 0:
        return "🔧 除以 0：v2 格式規定在進網路前就擋下（DIV_ZERO），手搓網路不用算。"
    if op == "ADD" and a + b > 127:
        return f"🔧 {a}+{b}：手搓網路目前只會總和 ≤127 的加法（v2 答案只有一個 7 位的詞，codec 會擋 ADD_OVERFLOW）。"
    if op == "SUB" and a < b:
        return f"🔧 {a}−{b}：手搓網路目前只會不是負數的減法（被減數 ≥ 減數，codec 會擋 SUB_UNDERFLOW）。"
    m = _load()
    import numpy as np
    codec = m.codec
    t = m.encode(getattr(codec, op), a, b)
    frame = np.array([[int(ch) for ch in codec.encode_input(t, "0" * 56, 3)]], dtype=np.int64)
    state, steps, done = m.run(m._net, frame)
    raw = "".join(str(int(v)) for v in state[0, m.o("OUT"):m.o("OUT") + codec.OUTPUT])
    try:
        vals = codec.decode_completed_answer(t, raw)["values"]
    except Exception as e:
        return f"🔧 手搓網路這題沒交出合法答案（{type(e).__name__}），作答欄：{raw}"
    if op == "MUL":
        got, gold = vals[0] * 128 + vals[1], a * b
        shown = f"{a} × {b} = **{got}**（網路寫的兩個詞：高 {vals[0]}、低 {vals[1]}，{vals[0]}×128＋{vals[1]}）"
        tail = "兩個資料詞＋句號 11111100"
    elif op == "DIV":
        got, gold = vals, divmod(a, b)
        shown = f"{a} ÷ {b} = **{vals[0]} 餘 {vals[1]}**"
        tail = "兩個資料詞＋句號 11111100"
    else:
        got, gold = vals[0], (a + b if op == "ADD" else a - b)
        shown = f"{a} {SYM[op]} {b} = **{got}**"
        tail = "一個資料詞＋句號 11111100"
    ok = "✅ 主機核對一致" if got == gold else f"❌ 主機核對不一致（應為 {gold}）"
    return (f"🔧 手搓網路算的：{shown}\n{ok}｜內部 {int(steps[0])} 步、對外交卷 1 次｜{len(m._net[1])} 個手填門檻單元，沒有訓練\n"
            f"題目 v2 位元：`{t}`\n答案前 24 位：`{raw[:24]}`（{tail}）")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(answer(" ".join(sys.argv[1:])))
    else:
        for s in ("手搓 33*3", "手搓 127÷5", "手搓 45+60", "手搓 45+67", "手搓 90-7", "手搓 7-90", "手搓 0+0", "手搓 127-127"):
            print(answer(s)); print("---")
