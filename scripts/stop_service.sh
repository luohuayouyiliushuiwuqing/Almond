#!/usr/bin/env bash
set -u
IFS=$'\n\t'

PORTS=(4000)
PATTERNS=("almond")          # cmdline 关键字，可多个
GRACE=3                      # SIGTERM 后等待秒数
SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--ports) IFS=',' read -r -a PORTS <<< "$2"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "未知参数：$1" >&2; exit 2;;
  esac
done

# 预授权 sudo，避免循环里反复弹密码
if [[ -n "$SUDO" ]]; then $SUDO -v || { echo "需要 sudo 权限"; exit 1; }; fi

declare -A KILLED   # pid -> reason
MYPID=$$

# ---- 工具函数 ----

# 递归收集 pid 及其所有子孙
collect_tree() {
  local root="$1"
  local stack=("$root")
  while ((${#stack[@]})); do
    local p="${stack[-1]}"; unset 'stack[-1]'
    echo "$p"
    for c in $(pgrep -P "$p" 2>/dev/null); do
      stack+=("$c")
    done
  done
}

# 优雅杀：先 TERM 整棵树，宽限后再 KILL
graceful_kill_tree() {
  local root="$1" reason="$2"
  [[ -z "$root" || "$root" == "$MYPID" ]] && return
  kill -0 "$root" 2>/dev/null || return

  # 收集整棵树，跳过已处理过的
  local tree=()
  for p in $(collect_tree "$root"); do
    [[ "$p" == "$MYPID" ]] && continue
    [[ -n "${KILLED[$p]:-}" ]] && continue
    tree+=("$p")
    KILLED[$p]="$reason"
  done
  [[ ${#tree[@]} -eq 0 ]] && return

  # 1) 温柔：先 TERM 整棵树（从叶到根更好，但简化为整组）
  $SUDO kill -TERM "${tree[@]}" 2>/dev/null

  # 2) 宽限等待
  local deadline=$(( $(date +%s) + GRACE ))
  while (( $(date +%s) < deadline )); do
    local alive=()
    for p in "${tree[@]}"; do kill -0 "$p" 2>/dev/null && alive+=("$p"); done
    [[ ${#alive[@]} -eq 0 ]] && return
    sleep 0.2
  done

  # 3) 还不走 → KILL
  for p in "${tree[@]}"; do
    kill -0 "$p" 2>/dev/null && $SUDO kill -KILL "$p" 2>/dev/null
  done
}

# 通过 cmdline 找 almond 进程（含子进程也会通过 collect_tree 覆盖）
find_by_cmdline() {
  for pat in "${PATTERNS[@]}"; do
    pgrep -f -- "$pat" 2>/dev/null
  done | sort -u
}

# 通过监听端口找 pid（ss 优先，netstat 兜底）
find_by_port() {
  local port="$1"
  if command -v ss >/dev/null; then
    $SUDO ss -lntpH 2>/dev/null \
      | awk -v p=":${port}$" '$4 ~ p' \
      | grep -oE 'pid=[0-9]+' | cut -d= -f2
  else
    $SUDO netstat -lntp 2>/dev/null \
      | awk -v p=":${port}$" '$4 ~ p' \
      | grep -oE '[0-9]+/' | cut -d/ -f1
  fi | sort -u
}

# 跳过 docker-proxy / containerd-shim，避免搞坏容器网络
is_docker_proxy() {
  local pid="$1"
  [[ -r "/proc/$pid/comm" ]] || return 1
  local c; c=$(cat "/proc/$pid/comm")
  [[ "$c" == "docker-proxy" || "$c" == "containerd-shim"* ]]
}

# ---- 1) 按 cmdline 清 ----
for pid in $(find_by_cmdline); do
  graceful_kill_tree "$pid" "命令行匹配 almond"
done

# ---- 2) 按端口兜底 ----
for port in "${PORTS[@]}"; do
  for pid in $(find_by_port "$port"); do
    if is_docker_proxy "$pid"; then
      echo "⚠ 端口 $port 被 docker-proxy 占用（PID $pid），跳过，请用 docker 命令停容器"
      continue
    fi
    graceful_kill_tree "$pid" "监听端口 $port"
  done
done

# ---- 3) 轮询确认端口释放（最多等 5s） ----
port_is_busy() {
  local port="$1"
  if command -v ss >/dev/null; then
    $SUDO ss -lntH 2>/dev/null | awk '{print $4}' | grep -qE ":${port}$"
  else
    $SUDO netstat -lnt 2>/dev/null | awk '{print $4}' | grep -qE ":${port}$"
  fi
}

busy=()
deadline=$(( $(date +%s) + 5 ))
for port in "${PORTS[@]}"; do
  while (( $(date +%s) < deadline )); do
    port_is_busy "$port" || break
    sleep 0.3
  done
  port_is_busy "$port" && busy+=("$port")
done

# ---- 输出 ----
if [[ ${#KILLED[@]} -eq 0 ]]; then
  echo "没有发现 almond 进程或端口占用（$(IFS=', '; echo "${PORTS[*]}")）"
else
  for pid in "${!KILLED[@]}"; do
    echo "已处理 PID $pid（${KILLED[$pid]}）"
  done
fi

if [[ ${#busy[@]} -gt 0 ]]; then
  echo "⚠ 端口仍被占用：$(IFS=', '; echo "${busy[*]}")"
  for p in "${busy[@]}"; do
    echo "  PID 详情："
    $SUDO ss -lntp 2>/dev/null | grep -E ":${p}\b"
  done
  echo "  提示："
  echo "    - systemd 托管的话：sudo systemctl stop almond*"
  echo "    - 容器托管的话：docker ps | grep ${busy[0]} && docker stop <容器>"
  exit 1
fi

echo "端口已释放：$(IFS=', '; echo "${PORTS[*]}")"
exit 0