# Link_with_Agents

讓使用者、Claude Code、Codex，以及 Gemini（agy CLI）與 OpenRouter 免費模型在同一串討論中協作，透過本機網頁與 Telegram 對話，共用指定的工作目錄。每位 AI 保留各自的 session。

這是開發中的 Windows 本機工具快照；目前的 Codex 路徑是 CLI session 接續，尚未完成與既有 Codex app 對話的單一寫入端整合。

## 功能

- 電腦網頁與 Telegram 訊息雙向同步。
- 每個 Telegram 群組有獨立的討論房、紀錄與 Claude／Codex session。
- 「問兩位／只問 Claude／只問 Codex」保留提問對象標記。
- 切換唯讀／可寫模式，顯示目前工作與待處理訊息。
- Claude 被權限系統攔下的動作，可由使用者允許一次或拒絕。
- 手動要求繼續討論、停止目前回合或開新討論。
- Telegram token 使用目前 Windows 使用者的 DPAPI 加密保存。

## 2026-11 新增

- **四位 AI**：Claude、Codex 之外，Gemini（經 Antigravity 的 `agy` CLI，自己的資料夾可寫、其他唯讀、可自選模型並有額度保護）與「輝達」（OpenRouter 免費模型，只有點名才回）。點名：網頁按鈕、訊息開頭 `@Gemini`／`@輝達`、Telegram `/gemini`、`/nvidia`。
- **輝達的文字工具**（`nvidia_agent.py`）：模型用【最近對話】【搜尋對話】【網路搜尋】【讀檔】【寫檔】【跑程式】【用量】【寫筆記】等標記請程式代做；跑程式走 Python audit hook 沙盒＋Windows Job Object 資源上限（記憶體、CPU、單一行程）。超出範圍在發言裡 @Claude 申請。
- **終端機橋接**（`terminal_bridge.py`）：讓正在終端機開著的 Claude Code 接手房間裡 Claude 的回合，逾時改用備援。
- **看門狗**（`supervisor.py`）：常駐重開中樞，可暫停／恢復；`restart_when_idle.ps1` 等房間閒置才重開。
- **共用資料夾通知**（`shared_watch.py`）：有新檔／改檔就在指定房間貼一行；略過清單、同檔冷卻與合併時間可由共用資料夾裡的規則檔設定。
- **對話搜尋**（`search.py`）：搜尋全部房間的紀錄檔。
- **網頁**：右側各家用量表（讀各自的用量 JSON）、網頁有新版本時出現「有更新」按鈕、Telegram 長回覆自動分段。
- 給 AI 的每則訊息都附時間；每次最多帶最近 60 則。

程式裡的資料夾路徑是作者這台電腦的配置（例如共用資料夾、各 AI 的工作資料夾），在別台電腦使用前要改成自己的路徑；共用工作區可用 `LINK_WITH_WORKDIR` 指定。

## 環境

- Windows 與 Python 3.10 以上，僅使用 Python 標準函式庫。
- 已安裝並登入的 Claude Code 與 Codex CLI；程式會尋找既有安裝，不會替你登入。
- 自己建立的 Telegram bot，以及可連線 Telegram 的網路。
- 網頁的 Markdown 顯示使用 CDN 上的 marked 與 DOMPurify；載入失敗時退回純文字。

## 啟動

1. 下載本倉庫。
2. 在本目錄執行以下指令，依隱藏提示輸入自己的 Telegram bot token：

~~~text
python secret_store.py set
~~~

3. 指定兩位 AI 共用的工作目錄，再啟動。例如在 Windows 命令提示字元：

~~~bat
set "LINK_WITH_WORKDIR=C:\Work\my-project"
python hub.py
~~~

也可雙擊「開啟三方討論.cmd」。未設定 LINK_WITH_WORKDIR 時，預設使用啟動時的目前目錄。

4. 使用程式開啟的本機網頁網址。
5. 第一次啟動時，將顯示的 /pair 配對碼私訊給自己的 bot，完成主人配對。

程式只監聽 127.0.0.1:8765。含 k 參數的網頁網址是本機存取憑證，應和 secrets 資料夾一樣保留在本機。此工具沒有對外提供 HTTP 服務的部署設定。

啟動時會分別執行 Claude 回話與 Codex 指令的自我檢查，會使用各自登入帳號的模型額度。這不是離線語法檢查。

## Telegram 指令

| 指令 | 用途 |
| --- | --- |
| /claude 問題 | 只問 Claude |
| /codex 問題 | 只問 Codex |
| /gemini 問題 | 只問 Gemini |
| /nvidia 問題 | 只問輝達（OpenRouter 免費模型） |
| /note 文字 | 只記下，不叫任何 AI |
| /auto 3 | 設定自動輪流上限 |
| /more 2 | 再討論兩輪 |
| /order | 切換先發言者 |
| /stop | 停止這個房間的目前回合 |
| /new | 這個房間開新討論 |
| /write on 或 /write off | 切換全域寫檔模式 |
| /allow 或 /deny | 允許一次或拒絕該房間的權限請求 |
| /bind 或 /unbind | 綁定或靜音目前群組 |

目前只接受已配對主人的 Telegram 訊息；不是多人共同授權的群組管理系統。群組房間之間有獨立 AI session，但共用工作目錄中的檔案仍需協調分工。

## 紀錄、權限與版本保存

- secrets/、tg_state*.json、logs/、模型 session ID、聊天 ID、配對狀態及登入資料不屬於原始碼，不應提交。
- 唯讀／可寫模式會使用各 CLI 支援的權限設定；它不是本工具額外實作的一個 OS 安全邊界。
- 不要讓 app 與另一個 CLI 程序同時續接同一個 Codex thread；既有寫入者會造成 active writer conflict。
- 目前沒有部署自動 Git 推送或定時模型工作。維護者整理一批可驗證的修改後，再提交、推送。
- 推送前先確認分工、遠端分支與提交內容；既有執行個體使用的資料仍保留在本機。

## 作者與入庫範圍

聊天程式由 Claude 撰寫並維護；Codex 協助原始碼封存與通用工作目錄設定整理。

倉庫只保存程式、啟動檔與必要使用說明。聊天內容、記憶／狀態、使用者指示、需求清單、實驗資料與憑證均留在本機。
