r"""雲端備份：研究倉庫＋聊天日誌＋時間戳收據 → 使用者自己的 Google 雲端硬碟（J:，電腦版會自動上傳）。
使用者 11-20 03:14：「還要預防硬碟陣亡，你們可以弄備份在 D 或是谷哥鏡像，谷哥硬碟有 5T 很大」
D 槽那份 Codex 已做（Git 倉庫，03:32 驗證可還原）；這支做第二份、放在不同地點（雲端）。

- Git 倉庫：`git bundle --all` 打包成一個檔（可直接 `git clone xxx.bundle` 還原）；**只有分支有變才重打**，省上傳量。
- 聊天日誌、每日切片、書籤：增量複製（不刪雲端舊檔）。
- 時間戳收據：整個資料夾複製。
- 不含：secrets、金鑰、導師助手。
目的地：J:\我的雲端硬碟\研究備份\
用法：PY 雲端備份.py      （中樞每小時呼叫；沒變就幾乎不花時間）
"""
import hashlib, json, os, shutil, subprocess, sys, time

DEST = r"J:\我的雲端硬碟\研究備份"
REPOS = {"螺旋語研究": r"H:\Codex用\Rasengan_Binary_Creator", "共用區": r"H:\Agent共用", "三方討論中樞": r"H:\Claude用\三方討論"}
COPY = {"聊天日誌": r"H:\Claude用\三方討論\logs", "聊天日誌-按日": r"H:\Claude用\三方討論\logs-按日", "時間戳": r"H:\Agent共用\時間戳"}
STATE = os.path.join(DEST, "_狀態.json")


def refs_sig(repo):
    out = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname) %(objectname)"], capture_output=True, text=True, encoding="utf-8").stdout
    return hashlib.sha256(out.encode()).hexdigest()


def run():
    if not os.path.isdir(os.path.dirname(DEST)):
        return "❌ 找不到 Google 雲端硬碟（J:）——電腦版沒開？"
    os.makedirs(DEST, exist_ok=True)
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    done = []
    for name, repo in REPOS.items():
        sig = refs_sig(repo)
        if st.get(name) == sig:
            continue
        # 螺旋語倉庫全量打包 7.8 GB：第一次全量（name.bundle），之後只打「上次以後新增的」（name-增量-時間.bundle），還原時依序 fetch
        heads = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(objectname)"], capture_output=True, text=True, encoding="utf-8").stdout.split()
        prev = st.get(name + "_heads") or []
        full = not os.path.exists(os.path.join(DEST, f"{name}.bundle")) or not prev
        fname = f"{name}.bundle" if full else f"{name}-增量-{time.strftime('%Y%m%d-%H%M')}.bundle"
        tmp = os.path.join(os.environ.get("TEMP", "."), fname)
        cmd = ["git", "-C", repo, "bundle", "create", tmp, "--all"] + ([] if full else ["--not"] + prev)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0 and "empty bundle" in (r.stderr or ""):
            st[name], st[name + "_heads"] = sig, heads   # 只是分支指標移動、沒有新物件
            continue
        if r.returncode != 0:
            done.append(f"{name} 打包失敗：{r.stderr.strip()[:80]}")
            continue
        v = subprocess.run(["git", "bundle", "verify", tmp], capture_output=True, text=True, encoding="utf-8", cwd=repo)
        if v.returncode != 0:
            done.append(f"{name} 驗證失敗")
            continue
        shutil.copy(tmp, os.path.join(DEST, fname))
        os.remove(tmp)
        st[name], st[name + "_heads"] = sig, heads
        done.append(f"{name} {'全量' if full else '增量'}（{os.path.getsize(os.path.join(DEST, fname)) / 1e6:.1f} MB）")
    for name, src in COPY.items():
        if os.path.isdir(src):
            subprocess.run(["robocopy", src, os.path.join(DEST, name), "/E", "/XO", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                           capture_output=True, creationflags=0x08000000)
    st["最後"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return "☁️ 雲端備份：" + ("；".join(done) if done else "倉庫沒變，只同步了日誌與收據")


if __name__ == "__main__":
    print(run())
