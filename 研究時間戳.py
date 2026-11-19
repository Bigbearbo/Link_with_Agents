r"""研究時間戳（每天一次）：把各研究倉庫的版本指紋送第三方時間戳服務（FreeTSA，RFC 3161），收據存本機。
使用者 11-20 03:01–03:09：預印本是第一重保險、Git 第二重；Claude 指出本機 Git 時間可改 → 「可以，你們去弄」。
Codex 03:11 已完成第一份（只含螺旋語倉庫）；這支沿用同一做法，擴到所有研究倉庫，每天自動跑。

送出去的**只有一個 SHA-256 指紋**（manifest.json 的雜湊），內容、檔名、路徑都不外送。
收據：H:\Agent共用\時間戳\<日期>\{manifest.json, manifest.tsq, manifest.tsr, 驗證.txt}
復驗：openssl ts -verify -data manifest.json -in manifest.tsr -CAfile cacert.pem -untrusted tsa.crt（憑證沿用 Codex 那份）

用法：PY 研究時間戳.py          今天還沒蓋就蓋一份
      PY 研究時間戳.py --強制   再蓋一份
"""
import hashlib, json, os, shutil, subprocess, sys, time, urllib.request

REPOS = {
    "螺旋語研究（Rasengan_Binary_Creator）": r"H:\Codex用\Rasengan_Binary_Creator",
    "共用區（研究想法、手搓、課程）": r"H:\Agent共用",
    "三方討論中樞": r"H:\Claude用\三方討論",
}
OUT = r"H:\Agent共用\時間戳"
OPENSSL = r"C:\Program Files\Git\usr\bin\openssl.exe"
CERTS = r"H:\Codex用\outputs\research-timestamps\20261120-0309"   # Codex 已下載的 FreeTSA 公開憑證
TSA = "https://freetsa.org/tsr"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def manifest():
    m = {"schema": "research-fingerprint-manifest-v1", "captured_local": time.strftime("%Y-%m-%d %H:%M:%S"), "repos": []}
    for name, path in REPOS.items():
        if not git(path, "rev-parse", "--git-dir"):   # Codex 03:32：螺旋語是 worktree，.git 是檔案不是資料夾
            m["repos"].append({"name": name, "error": "不是 git 倉庫"})
            continue
        refs = []
        for line in git(path, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines():
            ref, sha = line.split()
            refs.append({"ref": ref, "commit": sha, "tree": git(path, "rev-parse", sha + "^{tree}")})
        m["repos"].append({"name": name, "HEAD": git(path, "rev-parse", "HEAD"), "branches": refs})
    return m


def stamp(force=False):
    day = time.strftime("%Y%m%d")
    d = os.path.join(OUT, day)
    if os.path.exists(os.path.join(d, "manifest.tsr")) and not force:
        return f"今天已蓋過：{d}"
    if force and os.path.exists(d):
        d = os.path.join(OUT, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(d, exist_ok=True)
    mf = os.path.join(d, "manifest.json")
    json.dump(manifest(), open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tsq, tsr = os.path.join(d, "manifest.tsq"), os.path.join(d, "manifest.tsr")
    subprocess.run([OPENSSL, "ts", "-query", "-data", mf, "-sha256", "-cert", "-out", tsq], check=True, capture_output=True)
    req = urllib.request.Request(TSA, data=open(tsq, "rb").read(), headers={"Content-Type": "application/timestamp-query"})
    open(tsr, "wb").write(urllib.request.urlopen(req, timeout=60).read())
    for c in ("cacert.pem", "tsa.crt"):
        shutil.copy(os.path.join(CERTS, c), d)
    v = subprocess.run([OPENSSL, "ts", "-verify", "-data", mf, "-in", tsr, "-CAfile", os.path.join(d, "cacert.pem"),
                        "-untrusted", os.path.join(d, "tsa.crt")], capture_output=True, text=True)
    info = subprocess.run([OPENSSL, "ts", "-reply", "-in", tsr, "-text"], capture_output=True, text=True).stdout
    when = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.startswith("Time stamp")), "?")
    sha = hashlib.sha256(open(mf, "rb").read()).hexdigest()
    ok = "OK" in (v.stdout + v.stderr)
    open(os.path.join(d, "驗證.txt"), "w", encoding="utf-8").write(
        f"第三方時間：{when}（UTC）\nmanifest.json SHA-256：{sha}\n驗證：{'通過' if ok else '失敗'}\n{v.stdout}{v.stderr}\n")
    return f"{'✅' if ok else '❌'} 時間戳 {when} UTC｜{len(REPOS)} 個倉庫｜收據 {d}"


if __name__ == "__main__":
    print(stamp("--強制" in sys.argv))
