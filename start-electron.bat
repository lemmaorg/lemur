@echo off
REM ── Full dev launcher: Electron ─────────────────────────────────────
REM Runs the same Electron shell that ships in the installers, including
REM all native features: Discord Rich Presence, the native folder picker,
REM and the custom frameless window controls. Use THIS to test those.
REM Requires `npm install` to have been run once.
title Lemur (Electron dev launcher)
cd /d "%~dp0"

REM ── Clear any stale server squatting on port 5000 ──────────────────
REM Orphaned dev servers from a crashed/closed previous run keep serving an
REM OLD page, which is why code changes sometimes "don't show up". Kill them.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5000 " ^| findstr LISTENING') do (
  taskkill /PID %%P /F >nul 2>&1
)

call npm start
