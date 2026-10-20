@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo 正在启动罢工 / 游行监测（每 5 分钟刷新，关掉这个窗口即停止）...
python monitor.py --watch 300 --serve 8765 --open
echo.
echo 监测已停止。按任意键关闭。
pause >nul
