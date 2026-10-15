#!/usr/bin/env bash
# 构建 React 前端：vite build → almond/webui/dist/
#
# 这个 dist/ 是纳入版本控制的（后端 almond/webui/staticfiles.py 直接挂 /app，
# 装好包就能跑，运行环境不需要 node）。所以构建成功后记得把 dist 一起提交。
# vite 配了 emptyOutDir=true，每次构建会先清空该目录 —— 本脚本因此在构建前
# 备份、失败时还原，避免把已入库的前端产物留在半删状态。
#
# 用法：
#   ./scripts/build.sh                构建（缺 node_modules 时自动装依赖）
#   ./scripts/build.sh --ci           强制 npm ci（严格按 package-lock.json，会先清空 node_modules）
#   ./scripts/build.sh --skip-deps    跳过装依赖，直接构建（node_modules 已就位时最快）
#   ./scripts/build.sh --dev          vite dev 服务器（5173，反代 /api 到后端），不是构建
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

FRONTEND="$ROOT/frontend"
DIST="$ROOT/almond/webui/dist"

MODE=build
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ci)         MODE=ci; shift ;;
    --skip-deps)  MODE=skip-deps; shift ;;
    --dev)        MODE=dev; shift ;;
    -h|--help)    grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

# ---- 前置检查 ----
command -v npm >/dev/null || { echo "找不到 npm，请先装 Node.js（Vite 5 需要 Node ≥ 18）" >&2; exit 1; }
NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v//' | cut -d. -f1)"
[[ -n "$NODE_MAJOR" && "$NODE_MAJOR" -ge 18 ]] || {
  echo "Node 版本过低（当前 v${NODE_MAJOR:-未知}），Vite 5 需要 ≥ 18" >&2; exit 1; }
[[ -d "$FRONTEND" ]] || { echo "找不到前端目录 $FRONTEND" >&2; exit 1; }

# ---- 依赖 ----
# package.json 把 Windows 专用的 @esbuild/win32-x64 写进了 dependencies（lock 里 optional=None），
# 于是在任何非 win32 机器上 npm 都直接 EBADPLATFORM 退出。非 Windows 平台补一个 --force
# 跳过平台校验：代价是多装一个用不到的 win32 二进制，换来 linux/macOS 能正常装依赖。
# 要根治是把那条依赖删掉（esbuild 自己已按 optionalDependencies 提供各平台二进制）。
NPM_FLAGS=()
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*|Windows*) ;;
  *) NPM_FLAGS+=(--force) ;;
esac

if [[ "$MODE" == ci ]]; then
  echo "→ npm ci（按 package-lock.json 全新安装）${NPM_FLAGS[*]:+${NPM_FLAGS[*]}}"
  npm --prefix "$FRONTEND" ci ${NPM_FLAGS[@]+"${NPM_FLAGS[@]}"}
elif [[ ! -d "$FRONTEND/node_modules" ]]; then
  if [[ "$MODE" == skip-deps ]]; then
    echo "frontend/node_modules 不存在，无法跳过装依赖" >&2; exit 1
  fi
  if [[ -f "$FRONTEND/package-lock.json" ]]; then
    echo "→ node_modules 缺失，执行 npm ci${NPM_FLAGS[*]:+ ${NPM_FLAGS[*]}}"
    npm --prefix "$FRONTEND" ci ${NPM_FLAGS[@]+"${NPM_FLAGS[@]}"}
  else
    echo "→ node_modules 缺失且无 lockfile，执行 npm install${NPM_FLAGS[*]:+ ${NPM_FLAGS[*]}}"
    npm --prefix "$FRONTEND" install ${NPM_FLAGS[@]+"${NPM_FLAGS[@]}"}
  fi
fi

# ---- dev 模式：交给 vite，不做产物校验 ----
if [[ "$MODE" == dev ]]; then
  echo "→ vite dev（Ctrl+C 退出；API 反代目标见 VITE_API_TARGET）"
  exec npm --prefix "$FRONTEND" run dev
fi

# ---- 构建：失败则还原 dist ----
BACKUP=""
if [[ -d "$DIST" ]]; then
  BACKUP="$(mktemp -d "${TMPDIR:-/tmp}/almond-dist.XXXXXX")"
  cp -a "$DIST/." "$BACKUP/"
  trap '
    if [[ -n "$BACKUP" && -d "$BACKUP" ]]; then
      rm -rf -- "$DIST" && cp -a "$BACKUP/." "$DIST" && echo "已还原构建前的 dist/（原产物未受损）"
      rm -rf -- "$BACKUP"
    fi
  ' EXIT
fi

echo "→ vite build"
npm --prefix "$FRONTEND" run build

# ---- 校验产物 ----
[[ -s "$DIST/index.html"      ]] || { echo "构建后缺少 $DIST/index.html" >&2; exit 1; }
[[ -d "$DIST/assets" && -n "$(ls -A "$DIST/assets")" ]] || { echo "构建后 $DIST/assets 为空" >&2; exit 1; }
trap - EXIT
if [[ -n "$BACKUP" ]]; then rm -rf -- "$BACKUP"; fi

echo
echo "构建产物：${DIST#$ROOT/}"
ls -l "$DIST" "$DIST/assets" | sed 's|^|  |'
echo
echo "index.html 引用："
grep -oE '/app/assets/[A-Za-z0-9._-]+\.(js|css)' "$DIST/index.html" | sort -u | sed 's|^|  |' || true

if command -v git >/dev/null && [[ -d "$ROOT/.git" ]]; then
  CHANGED="$(git status --porcelain -- almond/webui/dist || true)"
  if [[ -n "$CHANGED" ]]; then
    echo
    echo "⚠ dist/ 已入库且有变化，记得提交（文件名带 hash，旧资源会被删、新资源会新增）："
    printf '%s\n' "$CHANGED" | sed 's|^|  |'
  fi
fi

echo
echo "验证：python -m almond.webui.server 后打开 http://127.0.0.1:4000/app/"
exit 0
