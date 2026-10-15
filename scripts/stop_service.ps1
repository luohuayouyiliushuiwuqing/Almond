# stop_service.ps1 —— 杀掉 Almond 服务进程并释放端口（增强版）
#
# 相比初版：
#   - 杀整棵进程树（uvicorn --reload / multiprocessing 的子进程不再漏）
#   - 先优雅（taskkill /T，不带 /F）→ 宽限 → 强杀（/T /F）
#   - 跳过 docker 相关代理，避免搞坏容器网络
#   - 端口释放改成轮询，TIME_WAIT 不再误报
#   - 失败时打印 PID 详情 + systemd / docker 提示
#
# 用法（建议以管理员身份运行）：
#   powershell -ExecutionPolicy Bypass -File .\scripts\stop_service.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\stop_service.ps1 -Ports 4000
#   powershell -ExecutionPolicy Bypass -File .\scripts\stop_service.ps1 -Patterns "almond","almond-webui"

[CmdletBinding()]
param(
    [int[]]$Ports          = @(4000),
    [string[]]$Patterns    = @("almond"),
    [int]$GraceSeconds     = 3,
    [int]$PortWaitSeconds  = 5
)

$ErrorActionPreference = "SilentlyContinue"

# ---- 管理员检查 ----
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "当前不是管理员，可能杀不掉其他用户/服务启动的进程。建议以管理员身份运行。"
}

$MyPid  = $PID
$killed = @{}    # pid -> reason

# ---- 工具函数 ----

function Get-ProcessTree {
    param([int]$RootPid)
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $byParent = @{}
    foreach ($p in $all) {
        $ppid = [int]$p.ParentProcessId
        if (-not $byParent.ContainsKey($ppid)) { $byParent[$ppid] = @() }
        $byParent[$ppid] += $p
    }
    $result = New-Object System.Collections.Generic.List[object]
    $stack  = New-Object System.Collections.Stack
    $stack.Push($RootPid)
    $seen = @{}
    while ($stack.Count -gt 0) {
        $cur = [int]$stack.Pop()
        if ($seen.ContainsKey($cur)) { continue }
        $seen[$cur] = $true
        $node = $all | Where-Object { $_.ProcessId -eq $cur } | Select-Object -First 1
        if ($node) { $result.Add($node) }
        if ($byParent.ContainsKey($cur)) {
            foreach ($c in $byParent[$cur]) { $stack.Push([int]$c.ProcessId) }
        }
    }
    return $result
}

function Test-DockerProxy {
    param([string]$Name)
    if (-not $Name) { return $false }
    return $Name -match '^(docker-proxy|com\.docker\.backend|containerd-shim|wslrelay)'
}

function Stop-ProcessTree {
    param([int]$RootPid, [string]$Reason)
    if ($RootPid -le 0 -or $RootPid -eq $MyPid) { return }
    if ($killed.ContainsKey($RootPid)) { return }
    if (-not (Get-Process -Id $RootPid -ErrorAction SilentlyContinue)) { return }

    $tree = Get-ProcessTree -RootPid $RootPid
    $pids = @()
    foreach ($node in $tree) {
        $id = [int]$node.ProcessId
        if ($id -le 0 -or $id -eq $MyPid) { continue }
        if ($killed.ContainsKey($id)) { continue }
        $killed[$id] = $Reason
        $pids += $id
    }
    if ($pids.Count -eq 0) { return }

    # 1) 优雅：taskkill /T（不带 /F）→ 给控制台进程发 CTRL_CLOSE / WM_CLOSE
    foreach ($p in $pids) {
        & taskkill /PID $p /T 2>$null | Out-Null
    }

    # 2) 宽限等待
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        $alive = @()
        foreach ($p in $pids) {
            if (Get-Process -Id $p -ErrorAction SilentlyContinue) { $alive += $p }
        }
        if ($alive.Count -eq 0) { return }
        Start-Sleep -Milliseconds 200
    }

    # 3) 还不走 → 强杀整棵树
    foreach ($p in $pids) {
        if (Get-Process -Id $p -ErrorAction SilentlyContinue) {
            & taskkill /PID $p /T /F 2>$null | Out-Null
        }
    }
}

function Find-ByCommandLine {
    param([string[]]$Keys)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            if (-not $cmd) { return $false }
            foreach ($k in $Keys) {
                if ($k -and ($cmd -match $k)) { return $true }
            }
            return $false
        }
}

function Find-ByPort {
    param([int]$Port)
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 }
}

function Test-PortBusy {
    param([int]$Port)
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$c
}

# ---- 1) 按命令行清 ----
$byCmd = Find-ByCommandLine -Keys $Patterns
foreach ($p in $byCmd) {
    $name = $p.Name
    if (Test-DockerProxy -Name $name) { continue }
    Stop-ProcessTree -RootPid ([int]$p.ProcessId) -Reason "命令行匹配 $($Patterns -join '/')"
}

# ---- 2) 按端口兜底 ----
foreach ($port in $Ports) {
    foreach ($pid in (Find-ByPort -Port $port)) {
        $name = (Get-Process -Id $pid -ErrorAction SilentlyContinue).Name
        if (Test-DockerProxy -Name $name) {
            Write-Warning "端口 $port 被 docker 代理占用（PID $pid，$name），跳过；请用 docker 命令停容器"
            continue
        }
        Stop-ProcessTree -RootPid ([int]$pid) -Reason "监听端口 $port"
    }
}

# ---- 3) 轮询确认端口释放 ----
$busy = @()
foreach ($port in $Ports) {
    $deadline = (Get-Date).AddSeconds($PortWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortBusy -Port $port)) { break }
        Start-Sleep -Milliseconds 300
    }
    if (Test-PortBusy -Port $port) { $busy += $port }
}

# ---- 输出 ----
if ($killed.Count -eq 0) {
    Write-Output "没有发现 almond 进程或端口占用（$($Ports -join ', ')）"
} else {
    foreach ($entry in $killed.GetEnumerator()) {
        Write-Output "已处理 PID $($entry.Key)（$($entry.Value)）"
    }
}

if ($busy.Count -gt 0) {
    Write-Output "⚠ 端口仍被占用：$($busy -join ', ')"
    foreach ($p in $busy) {
        Write-Output "  端口 $p 监听详情："
        Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue |
            ForEach-Object {
                $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
                Write-Output "    PID $($_.OwningProcess)  $($proc.Name)  $($proc.Path)"
            }
    }
    Write-Output "  提示："
    Write-Output "    - 服务是 systemd/WSL 托管的：在 WSL 里 systemctl stop almond*"
    Write-Output "    - 是容器：docker ps | findstr $($busy[0]) 再 docker stop <容器>"
    exit 1
}
Write-Output "端口已释放：$($Ports -join ', ')"
exit 0