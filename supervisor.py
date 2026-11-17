"""三方討論中樞的常駐看門狗：不開視窗跑 hub.py，掛掉就自動重開。

用法（通常由工作排程器在登入時用 pythonw 啟動）：
    pythonw supervisor.py          常駐
    python supervisor.py stop      停止看門狗和中樞（之後不會自動重開，直到下次登入或手動啟動）
    python supervisor.py pause     暫停：看門狗留著，但中樞掛了不重開（更新程式前用）
    python supervisor.py resume    恢復自動重開（看門狗沒在跑就順便啟動）
    python supervisor.py toggle    在「開啟／暫停」之間切換；status 看目前狀態
    雙擊「看門狗開關.cmd」＝ toggle
輸出寫在 logs/hub.log、logs/supervisor.log。
"""
import os, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
STOP = os.path.join(HERE, "supervisor.stop")
PAUSE = os.path.join(HERE, "supervisor.pause")  # 存在＝暫停：中樞掛了不重開（更新程式時用）
PORT = 8765
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "supervisor.log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def port_busy():
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def python_exe():
    # pythonw 啟動看門狗時，中樞改用同目錄的 python.exe（輸出導到檔案，不會跳視窗）
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "python.exe")
    return cand if os.path.exists(cand) else exe


def single_instance():
    # 用一個本機埠當鎖，避免開兩隻看門狗
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", PORT + 1))
    except OSError:
        return None
    s.listen(1)
    return s


def run():
    lock = single_instance()
    if lock is None:
        log("已有看門狗在跑，這隻結束")
        return
    if os.path.exists(STOP):
        os.remove(STOP)
    log("看門狗啟動")
    fails = 0
    while not os.path.exists(STOP):
        if os.path.exists(PAUSE) or port_busy():  # 暫停中，或中樞已經在跑（例如手動開的）：只監看
            time.sleep(5)
            continue
        hub_log = os.path.join(LOGS, "hub.log")
        if os.path.exists(hub_log) and os.path.getsize(hub_log) > 5_000_000:
            os.replace(hub_log, hub_log + ".old")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        t0 = time.time()
        with open(hub_log, "a", encoding="utf-8") as out:
            p = subprocess.Popen([python_exe(), "hub.py", "--no-browser"], cwd=HERE, env=env,
                                 stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            log(f"啟動中樞 pid={p.pid}")
            while p.poll() is None:
                if os.path.exists(STOP):
                    p.terminate()
                    break
                time.sleep(2)
        if os.path.exists(STOP):
            break
        if os.path.exists(PAUSE):
            log(f"中樞結束（code={p.returncode}），看門狗暫停中，不重開")
            continue
        fails = fails + 1 if time.time() - t0 < 60 else 0
        wait = min(300, 5 * 2 ** min(fails, 6))  # 一直秒退就拉長間隔，不狂重啟
        log(f"中樞結束（code={p.returncode}），{wait} 秒後重開")
        time.sleep(wait)
    log("收到停止，看門狗結束")


def stop():
    open(STOP, "w").close()
    print("已要求停止：看門狗和中樞會在幾秒內結束。")


def status():
    hub = "執行中" if port_busy() else "沒在跑"
    if not is_running():
        dog = "沒在跑"
    else:
        dog = "暫停（中樞掛了不會重開）" if os.path.exists(PAUSE) else "開啟（中樞掛了會自動重開）"
    print(f"看門狗：{dog}｜中樞：{hub}")


def is_running():
    s = single_instance()
    if s is None:
        return True
    s.close()
    return False


def pause():
    open(PAUSE, "w").close()
    log("暫停")
    status()


def resume():
    if os.path.exists(PAUSE):
        os.remove(PAUSE)
    log("恢復")
    if not is_running():  # 看門狗本身沒在跑就把它叫起來（獨立程序、無視窗）
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([exe if os.path.exists(exe) else sys.executable, os.path.abspath(__file__)], cwd=HERE,
                         creationflags=NO_WINDOW | 0x00000008)  # DETACHED_PROCESS
        time.sleep(2)
    status()


def toggle():
    resume() if os.path.exists(PAUSE) or not is_running() else pause()


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["run"])[0]
    {"run": run, "stop": stop, "pause": pause, "resume": resume, "status": status, "toggle": toggle}[cmd]()
