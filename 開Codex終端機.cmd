@echo off
chcp 65001 >nul
rem Codex CLI interactive window - new independent session (user 11-19 23:29); finds newest codex.exe
set "CX="
for /f "delims=" %%d in ('dir /b /ad /o-d "%LOCALAPPDATA%\OpenAI\Codex\bin" 2^>nul') do if not defined CX if exist "%LOCALAPPDATA%\OpenAI\Codex\bin\%%d\codex.exe" set "CX=%LOCALAPPDATA%\OpenAI\Codex\bin\%%d\codex.exe"
if not defined CX ( echo 找不到 codex.exe，請叫 Claude 看看 & pause & exit /b 1 )
cd /d "H:\Codex用"
title Codex CLI（H:\Codex用）
"%CX%" -m gpt-6.2-sol -c model_reasoning_effort="medium"
if errorlevel 1 pause
