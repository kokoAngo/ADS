@echo off
REM 触发Workflow执行
REM 其他app可以调用此脚本来触发Prediction Workflow

echo %date% %time% - Triggered by external app >> "%~dp0run_workflow.flag"
echo Workflow triggered successfully!
