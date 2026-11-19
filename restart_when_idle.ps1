# 等三方討論中樞閒下來（兩位都沒在工作）再重啟，套用新版程式。
param([string]$HubArgs = "")
function Get-HubPy { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'hub\.py' -and $_.Name -match 'python' } }
function Test-Busy {
    foreach ($h in @(Get-HubPy)) {
        $kids = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($h.ProcessId)" | Where-Object { $_.Name -match 'claude|codex|agy|antigravity|python' }  # 11-18：漏算 Gemini（agy）、輝達（python worker），重開時把它們砍斷了
        if ($kids) { return $true }
    }
    # 還在等終端機 Claude 回覆的接力（inbox 有、outbox 還沒有）也算忙：重開會把後面的 Gemini、輝達接力弄丟
    foreach ($f in @(Get-ChildItem (Join-Path $PSScriptRoot 'terminal\inbox') -Filter *.json -ErrorAction SilentlyContinue)) {
        $o = Join-Path $PSScriptRoot ("terminal\outbox\" + $f.BaseName + ".txt")
        if (-not (Test-Path $o) -and -not (Test-Path ($o + ".done"))) { return $true }
    }
    return $false
}
while ($true) {
    if (-not (Test-Busy)) {
        Start-Sleep 5
        if (-not (Test-Busy)) { break }
    }
    Start-Sleep 5
}
Set-Content -Path (Join-Path $PSScriptRoot "planned-restart.flag") -Value (Get-Date -Format s) -Encoding utf8  # 看門狗看到就不當陣亡通報
foreach ($h in @(Get-HubPy)) {
    $parent = $h.ParentProcessId
    Stop-Process -Id $h.ProcessId -Force -ErrorAction SilentlyContinue
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$parent"
    if ($p -and $p.Name -eq 'cmd.exe') { Stop-Process -Id $parent -Force -ErrorAction SilentlyContinue }
}
for ($i = 0; $i -lt 20; $i++) {  # 等舊程式放掉 8765 埠，否則新的會啟動失敗
    if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) { break }
    Start-Sleep 1
}
Remove-Item (Join-Path $PSScriptRoot "__pycache__") -Recurse -Force -ErrorAction SilentlyContinue
# 權限等設定都存在 tg_state.json，不需要額外參數。
# 注意：這支腳本本身要用 Start-Process 獨立啟動（不要放在 Claude 的背景工作裡），
# 否則背景工作結束時，它開出來的中樞會被一起關掉。
$sup = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'supervisor\.py' -and $_.CommandLine -notmatch ' stop' }
if ($sup -and -not (Test-Path (Join-Path $PSScriptRoot 'supervisor.pause'))) { Write-Output "看門狗會自動重開中樞 $(Get-Date -Format HH:mm:ss)"; exit 0 }
Start-Process cmd.exe -ArgumentList "/c `"$PSScriptRoot\開啟三方討論.cmd`" $HubArgs" -WorkingDirectory $PSScriptRoot
Write-Output "已重啟三方討論中樞 $(Get-Date -Format HH:mm:ss)"
