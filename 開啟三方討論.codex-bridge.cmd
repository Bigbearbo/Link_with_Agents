@echo off
chcp 65001 >nul
"D:\GPT-Cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -E -X utf8 -B "D:\Codex用\outputs\codex-app-bridge\control.py" start --open-ui
if errorlevel 1 pause
