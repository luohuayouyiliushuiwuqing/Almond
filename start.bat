@echo off
REM 根目录便捷入口：双击即启动 Almond 看板。参数原样透传给 scripts\start_service.bat。
setlocal
call "%~dp0scripts\start_service.bat" %*
echo.
echo ===================================================================
echo  [启动脚本结束]
echo  - 如果上面没有显示服务运行日志，说明启动【前】就已经失败，
echo    请把上方红色 [X] 报错文字发给我，我来定位。
echo  - 如果显示了"Serving on http://127.0.0.1:端口"，请用该【实际端口】
echo    访问： http://127.0.0.1:实际端口/app/
echo    （端口被占用时会自动往后跳，不一定是 4000）
echo  - 关闭此窗口即可停止服务（或另用 scripts\stop_service.bat）。
echo ===================================================================
pause
