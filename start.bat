@echo off
REM 根目录便捷入口：双击即启动 Almond 看板。参数原样透传给 scripts\start_service.bat。
setlocal
"%~dp0scripts\start_service.bat" %*
