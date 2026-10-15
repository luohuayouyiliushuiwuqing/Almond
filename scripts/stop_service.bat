@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

REM ============================================================
REM  stop_service.bat —— 杀掉 Almond 进程并释放端口 (Windows)
REM
REM  用法：
REM    scripts\stop_service.bat          （默认 4000）
REM    scripts\stop_service.bat 4000
REM    scripts\stop_service.bat 4000,8000
REM
REM  建议以管理员身份运行。
REM ============================================================

set "PORTS=%~1"
if "%PORTS%"=="" set "PORTS=4000"
set "PATTERN=almond"
set "GRACE=3"

echo [*] Ports : %PORTS%
echo [*] Match : %PATTERN%
echo.

set /a KILLED=0

REM 管理员提示（不强制）
net session >nul 2>&1
if errorlevel 1 echo [!] 当前非管理员，可能杀不掉别人/服务启动的进程。

REM ---------- 1) 按命令行匹配 ----------
echo [1/3] 扫描命令行含 "%PATTERN%" 的进程 ...
REM '%%%%almond%%%%' 经两层解析后 WMIC 收到的是 '%almond%'
for /f "skip=1 tokens=1" %%P in ('wmic process where "commandline like '%%%%almond%%%%'" get processid 2^>nul') do (
    set "P=%%P"
    if defined P (
        set "P=!P: =!"
        if not "!P!"=="" if not "!P!"=="0" (
            call :kill_tree !P! "cmdline-match"
        )
    )
)

REM ---------- 2) 按端口兜底 ----------
echo.
echo [2/3] 扫描监听端口 ...
for %%R in (%PORTS:,= %) do (
    call :kill_port %%R
)

REM ---------- 3) 端口释放确认 ----------
echo.
echo [3/3] 校验端口是否释放 ...
set "BUSY="
for %%R in (%PORTS:,= %) do (
    call :is_port_busy %%R
    if !errorlevel! equ 1 set "BUSY=!BUSY! %%R"
)

if "%KILLED%"=="0" echo [i] 没有发现 almond 进程或端口占用。

if "!BUSY!"=="" (
    echo [OK] 端口已释放：%PORTS%
    endlocal & exit /b 0
) else (
    echo [X] 端口仍被占用：!BUSY!
    echo     排查：netstat -ano ^| findstr LISTENING ^| findstr ":<port>"
    echo     提示：systemd/WSL 托管 - wsl -e systemctl stop almond
    echo           Docker 托管    - docker ps ^| findstr ^<port^> ，然后 docker stop ^<容器^>
    endlocal & exit /b 1
)

REM ============================================================
REM 子过程
REM ============================================================

:kill_tree
REM  %1=PID  %2=原因
set "TPID=%~1"
if "%TPID%"=="" goto :eof
if "%TPID%"=="0" goto :eof

call :pid_alive %TPID%
if errorlevel 1 goto :eof

REM 先试优雅：taskkill /T（不带 /F），对 GUI/控制台可能触发关闭事件
taskkill /PID %TPID% /T >nul 2>&1
timeout /t %GRACE% /nobreak >nul 2>&1
call :pid_alive %TPID%
if errorlevel 1 (
    echo   - 已终止 PID %TPID% （优雅，%~2）
    set /a KILLED+=1
    goto :eof
)

REM 还在 → 强杀整棵树
taskkill /PID %TPID% /T /F >nul 2>&1
call :pid_alive %TPID%
if errorlevel 1 (
    echo   - 已终止 PID %TPID% （强杀，%~2）
    set /a KILLED+=1
) else (
    echo   ! 无法终止 PID %TPID% （%~2），可能需要管理员
)
goto :eof


:kill_port
REM  %1=端口
set "KPORT=%~1"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /i "LISTENING" ^| findstr ":%KPORT% "') do (
    set "P=%%P"
    if not "!P!"=="" if not "!P!"=="0" (
        REM 跳过 docker 相关代理（避免搞坏容器网络）
        tasklist /fi "PID eq !P!" /nh 2>nul | findstr /i "docker" >nul
        if !errorlevel! equ 0 (
            echo   [skip] 端口 %KPORT% 被 docker 代理占用 PID !P!，请用 docker 命令处理
        ) else (
            call :kill_tree !P! "port-%KPORT%"
        )
    )
)
goto :eof


:pid_alive
REM  存在返回 0，不存在返回 1
tasklist /fi "PID eq %~1" /nh 2>nul | findstr /r "[0-9]" >nul
if errorlevel 1 (exit /b 1) else (exit /b 0)


:is_port_busy
REM  占用返回 1，空闲返回 0
netstat -ano | findstr /i "LISTENING" | findstr ":%~1 " >nul
if errorlevel 1 (exit /b 0) else (exit /b 1)