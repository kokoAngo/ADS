@echo off
REM 启动Workflow监控服务
REM 双击运行此文件即可启动监控

title Fango Ads - Workflow Monitor

echo ============================================================
echo Fango Ads - Workflow Monitor
echo ============================================================
echo.
echo 监控已启动，等待触发信号...
echo 触发方式: 运行 trigger\trigger_workflow.bat
echo 按 Ctrl+C 停止
echo ============================================================
echo.

cd /d "%~dp0"
python scripts\workflow_trigger.py

pause
