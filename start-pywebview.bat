@echo off
REM ── Lightweight dev launcher: pywebview (Edge WebView2) ──────────────
REM Runs the Flask app in a native Edge window. Lighter than Electron, but
REM does NOT include the Electron-only features (Discord Rich Presence,
REM native folder picker, frameless window controls). For testing those,
REM and for what ships in the installers, use start-electron.bat instead.
title Lemur (pywebview dev launcher)
cd /d "%~dp0"

REM ── Clear any stale server squatting on port 5000 ──────────────────
REM Orphaned dev servers from a crashed/closed previous run keep serving an
REM OLD page, which is why code changes sometimes "don't show up". Kill them.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5000 " ^| findstr LISTENING') do (
  taskkill /PID %%P /F >nul 2>&1
)

python launch.py
