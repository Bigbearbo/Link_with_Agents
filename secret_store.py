"""目前 Windows 使用者的 DPAPI 加密保存；不印出秘密。

用法：  python secret_store.py set   （從 stdin 讀 token，或隱藏輸入）
"""
import ctypes
import ctypes.wintypes as wt
import getpass
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "secrets", "telegram-bot-token.dpapi")
MAGIC = b"RT-DPAPI1\x00"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = _Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("DPAPI 失敗")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def save_token(token: str):
    token = token.strip()
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{20,}", token):
        raise SystemExit("token 格式不對")
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "wb") as f:
        f.write(MAGIC + _crypt(token.encode(), False))


def load_token() -> str:
    with open(TOKEN_FILE, "rb") as f:
        data = f.read()
    if not data.startswith(MAGIC):
        raise ValueError("加密檔格式不明")
    return _crypt(data[len(MAGIC):], True).decode()


if __name__ == "__main__" and sys.argv[1:] == ["set"]:
    tok = sys.stdin.readline() if not sys.stdin.isatty() else getpass.getpass("Telegram token（隱藏輸入）：")
    save_token(tok)
    print("已用 DPAPI 加密保存，沒有建立明文檔。")
