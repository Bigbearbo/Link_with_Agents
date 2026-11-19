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


def hub_down_alert(code, recovered=False):
    """中樞陣亡通報（使用者 11-19 09:4x：「加一個中樞陣亡備用程式，判定陣亡後通知負責 AI 和傳陣亡訊息，以免大家開始猜怎麼沒回應」）。
    中樞死了群組也發不出去，所以這裡不經中樞，直接：
    ① Telegram 私訊使用者（owner）②留一張請求給終端機 Claude（負責修中樞；它的監看會被叫醒）
    ③在 Gemini、輝達家的 群組\\待回\\ 留「中樞陣亡」單，輝達的 OpenCode 另外直接叫醒。每次陣亡只通報一次，恢復再通報一次。"""
    import json as _j, urllib.request as _u
    tail = ""
    try:
        tail = "".join(open(os.path.join(LOGS, "hub.log"), encoding="utf-8", errors="replace").readlines()[-8:])[-1200:]
    except OSError:
        pass
    now = time.strftime("%m-%d %H:%M")
    msg = (f"✅ 中樞已恢復（{now}）。剛才那段時間的訊息可能沒被回覆，請重講一次重要的事。" if recovered else
           f"🚨 中樞陣亡（{now}，結束碼 {code}）：群組暫時收不到也發不出訊息，不是大家不理你。看門狗會自動重開；"
           "一直起不來的話，終端機的 Claude 已被叫去修。")
    try:  # ① Telegram（不經中樞）
        from secret_store import load_token
        owner = _j.load(open(os.path.join(HERE, "tg_state.json"), encoding="utf-8")).get("owner")
        if owner:
            data = _j.dumps({"chat_id": owner, "text": msg}).encode()
            _u.urlopen(_u.Request(f"https://api.telegram.org/bot{load_token()}/sendMessage", data=data,
                                  headers={"Content-Type": "application/json"}), timeout=20).read()
    except Exception as e:
        log(f"陣亡通報 Telegram 失敗：{type(e).__name__}")
    if not recovered:
        try:  # ② 給終端機 Claude 的請求（中樞重開後，Claude 寫的回覆會由中樞補貼回主討論）
            rid = time.strftime("%H%M%S") + "-hubdown"
            os.makedirs(os.path.join(HERE, "terminal", "inbox"), exist_ok=True)
            _j.dump({"id": rid, "room": "主討論", "time": time.strftime("%m-%d %H:%M:%S"),
                     "body": f"（看門狗自動通報）{msg}\n\n中樞最後的紀錄：\n{tail}\n\n請 Claude 查原因、修好，回覆會在中樞恢復後貼出。"},
                    open(os.path.join(HERE, "terminal", "inbox", rid + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception as e:
            log(f"陣亡通報給 Claude 失敗：{type(e).__name__}")
    for home in (r"H:\Gemini用", r"H:\免費模型用"):  # ③ 視窗 agent
        try:
            box = os.path.join(home, "群組", "待回")
            os.makedirs(box, exist_ok=True)
            p = os.path.join(box, "中樞陣亡.md")
            if recovered:
                if os.path.exists(p):
                    os.remove(p)
            else:
                open(p, "w", encoding="utf-8").write(f"# 中樞陣亡（{now}）\n{msg}\n這段時間 群組.py 和發送箱會失敗，先做手上的事，不用回群組；中樞恢復後這張單會自動消失。\n")
        except OSError:
            pass
    if not recovered:
        try:
            import window_agent as wa
            wa.nudge("輝達", "g1004308131170", custom="【看門狗通知】中樞陣亡，群組暫時不通；先做手上的事，不用回群組，恢復後會再通知。")
        except Exception:
            pass
    log("陣亡通報已送出" if not recovered else "恢復通報已送出")


def run():
    lock = single_instance()
    if lock is None:
        log("已有看門狗在跑，這隻結束")
        return
    if os.path.exists(STOP):
        os.remove(STOP)
    log("看門狗啟動")
    fails = 0
    down_alerted = False
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
            alerted_ok, ping_fail, last_ping = False, 0, time.time()
            while p.poll() is None:
                if time.time() - t0 > 90 and time.time() - last_ping > 20:  # 卡死（程式還在、但不回應）也算陣亡
                    last_ping = time.time()
                    try:
                        import urllib.request as _u
                        _u.urlopen("http://127.0.0.1:8765/ping", timeout=10).read()
                        ping_fail = 0
                    except Exception:
                        ping_fail += 1
                    if ping_fail >= 4:
                        log("中樞 80 秒沒回應，判定卡死，關掉重開")
                        if not down_alerted:
                            hub_down_alert("卡死")
                            down_alerted = True
                        p.terminate()
                        break
                if down_alerted and not alerted_ok and time.time() - t0 > 60:  # 撐過一分鐘＝真的恢復了
                    hub_down_alert(0, recovered=True)
                    down_alerted, alerted_ok = False, True
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
        # 正常重開（restart_when_idle 送的 code=0 或手動關）不算陣亡；非 0 結束、或連續秒退才通報
        planned = os.path.join(HERE, "planned-restart.flag")   # restart_when_idle.ps1 關中樞前會寫這個記號
        is_planned = os.path.exists(planned) and time.time() - os.path.getmtime(planned) < 600
        if is_planned and fails < 2:
            os.remove(planned)
        elif (p.returncode not in (0, None) or fails >= 2) and not down_alerted:
            hub_down_alert(p.returncode)
            down_alerted = True
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
