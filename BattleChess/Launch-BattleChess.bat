@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-BattleChess.ps1"
if errorlevel 1 pause
