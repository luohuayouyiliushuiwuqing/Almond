@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

REM ============================================================
REM  start_service.bat —— 一键启动 Almond 看板（Windows）
REM
REM  用法：
REM    scripts\start_service.bat                         默认 127.0.0.1:4000（免 token，仅本机）
REM    scripts\start_service.bat --rebuild               先重新构建前端再启动
REM    scripts\start_service.bat --host 0.0.0.0 --token 值   暴露到局域网（非回环必须带 token）
REM
REM  说明：
REM    - 后端用 conda 环境 ximages 的 python（scrapling / almond 装在那）；找不到就退而用 PATH 上的 python。
REM    - 前端 dist/ 已入库；若缺失会自动 vite build（node 需在 PATH 上）。
REM    - 端口被占用时自动从 4000 往后跳（最多 20 个），以日志里打印的实际端口为准。
REM    - 前台运行：本窗口即服务日志，Ctrl+C 停止；另提供了 scripts\stop_service.bat 可强停。
REM ============================================================

REM ---- 定位仓库根（脚本在 scripts\ 下，根即上级目录）----
set "ROOT=%~dp0.."
cd /d "%ROOT%" || ( echo [X] 无法切到仓库根目录 & pause & exit /b 1 )
REM 规范化 ROOT 为绝对路径（去掉 ..，避免后续拼接歧义）
set "ROOT=%CD%"

REM ---- 选择 python（优先 ximages 环境，否则 PATH 上的 python）----
set "PY="
if exist "%USERPROFILE%\miniconda3\envs\ximages\python.exe" (
    set "PY=%USERPROFILE%\miniconda3\envs\ximages\python.exe"
) else (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] 找不到 python：请安装 Miniconda 并创建 ximages 环境，或把 python 加到 PATH。
    pause & exit /b 1
)

REM ---- 校验 almond 包可导入（顺便确认环境正确）----
"%PY%" -c "import almond.webui.server" 2>nul
if errorlevel 1 (
    echo [X] 无法导入 almond 包（当前目录：%CD%）。
    echo     请使用项目环境（ximages），并确保从仓库根目录运行。
    pause & exit /b 1
)

REM ---- 解析参数：剥离 --rebuild，其余透传给 server ----
set "REBUILD=0"
set "ARGS="
:parse
if "%~1"=="" goto :done_parse
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="--rebuild" ( set "REBUILD=1" ) else ( set "ARGS=!ARGS! %~1" )
shift
goto :parse
:done_parse

REM ---- 前端构建（强制 / 产物缺失时自动）----
if "%REBUILD%"=="1" call :build
if not exist "%ROOT%\almond\webui\dist\index.html" (
    echo [!] 未找到前端产物 almond/webui/dist/index.html，自动构建一次…
    call :build
)

REM ---- 组装 server 参数：缺省补 127.0.0.1 + 探测空闲端口 ----
set "SRV_ARGS="
set "T=!ARGS!"
if "!T:--host=!"=="!T!" set "SRV_ARGS=!SRV_ARGS! --host 127.0.0.1"
if "!T:--port=!"=="!T!" (
    REM 启动前先探测一个确定空闲的端口，显式传给 server，避免被二次跳端口搞混
    set "PORT="
    for /f "delims=" %%P in ('"%PY%" "%ROOT%\scripts\_free_port.py" 2^>nul') do set "PORT=%%P"
    if not defined PORT set "PORT=4000"
    set "SRV_ARGS=!SRV_ARGS! --port !PORT!"
)
set "SRV_ARGS=!SRV_ARGS! !ARGS!"

echo.
echo [*] 启动 Almond 看板：
echo     %PY% -m almond.webui.server!SRV_ARGS!
if defined PORT (
    echo     本机访问：http://127.0.0.1:!PORT!/app/
    echo     （脚本已选空闲端口，日志中 "Serving on" 的端口与此一致）
) else (
    echo     本机访问：http://127.0.0.1:^<你用 --port 指定的端口^>/app/
)
echo.
"%PY%" -m almond.webui.server !SRV_ARGS!
echo.
echo Almond 已停止。
pause
goto :eof

REM ============================================================
:build
echo [*] 构建前端（npm --prefix frontend run build）…
where npm >nul 2>&1 || ( echo [X] 找不到 npm，请安装 Node.js（Vite 5 需 Node ^>= 18）。 & pause & exit /b 1 )
pushd "%ROOT%\frontend"
call npm run build
popd
if not exist "%ROOT%\almond\webui\dist\index.html" (
    echo [X] 构建失败：缺少 almond/webui/dist/index.html
    pause & exit /b 1
)
echo [OK] 前端构建完成。
goto :eof

REM ============================================================
:usage
echo 用法：start_service.bat [--rebuild] [--host HOST] [--port PORT] [--token TOKEN]
echo   --rebuild       启动前重新构建前端
echo   其余参数（--host / --port / --token）直接透传给 almond.webui.server
echo.
echo   默认仅监听 127.0.0.1（本机、免 token）。要暴露到局域网请加：
echo     --host 0.0.0.0 --token ^<值^>
echo   （绑非回环地址必须带 token，否则服务会拒绝启动。）
pause
exit /b 0
