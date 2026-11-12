@echo off
rem hub strong-mode launcher: per-run token required (MIO_TASKHUB_RUN_TOKEN_REQUIRED=1)
rem WMI/手动拉起 hub 时走本脚本；直接启动 exe 会静默回到宽松模式。
rem exe 相对 packaging/ 的上一级 dist/（即仓库内 dist\mio-taskhub-v3\TaskHub.exe）。
set MIO_TASKHUB_RUN_TOKEN_REQUIRED=1
cd /d "%~dp0.."
"dist\mio-taskhub-v3\TaskHub.exe"
