#!/usr/bin/env bash
# 清理「可再生」的构建产物：Python 字节码、setuptools 打包输出、Vite 缓存。
#
# 安全边界（刻意不碰，删了会丢数据或脏工作区）：
#   almond/webui/dist/  React 构建产物，已纳入版本控制，由 vite 的 emptyOutDir 自管
#   metadata.db*        看板数据库      config.json  存储配置
#   cache/              图片本地缓存    _tmp/        本地测试数据
#   .workbuddy/         项目本地数据    *.pem        插件签名私钥
# 因此这里不用 git clean（-x 会连上面这些一起删掉）。
#
# 用法：
#   ./scripts/clean.sh              清字节码 + 打包输出 + Vite 缓存
#   ./scripts/clean.sh -n           只看会删什么，不实际删
#   ./scripts/clean.sh --deps       连带删 frontend/node_modules（重装需要网络，故默认保留）
set -u
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

DRY=0
DEPS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run) DRY=1; shift ;;
    --deps)       DEPS=1; shift ;;
    -h|--help)    grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

targets=()

# 1) Python 字节码。__pycache__ 打印后立刻 prune，避免把里面的 .pyc 再列一遍（重复且会先被父目录删掉）
while IFS= read -r p; do
  targets+=("$p")
done < <(find . \
           \( -name .git -o -name node_modules \) -prune \
           -o -name '__pycache__' -print -prune \
           -o \( -name '*.pyc' -o -name '*.pyo' \) -print)

# 2) 打包输出：只认仓库根的 build/ 与 dist/。
#    绝不能用 find -name dist —— 那会命中已入库的 almond/webui/dist/
shopt -s nullglob
for p in ./build ./dist ./*.egg-info; do
  [[ -e "$p" ]] && targets+=("$p")
done

# 3) Vite 依赖预构建缓存
[[ -e ./frontend/.vite ]] && targets+=("./frontend/.vite")

# 4) 前端依赖（opt-in）
(( DEPS )) && [[ -e ./frontend/node_modules ]] && targets+=("./frontend/node_modules")

# ---- 报告与执行 ----
if [[ ${#targets[@]} -eq 0 ]]; then
  echo "没有可清理的构建产物（已是干净状态）"
  echo "保留项：almond/webui/dist/（已入库）、metadata.db、config.json、cache/、_tmp/"
  exit 0
fi

total_kb=0
for p in "${targets[@]}"; do
  kb=$(du -sk -- "$p" 2>/dev/null | cut -f1)
  [[ -n "$kb" ]] && total_kb=$(( total_kb + kb ))
done

# 不足 1 MB 时按 KB 显示，否则会打印没信息量的「约 0 MB」
if (( total_kb < 1024 )); then
  human="${total_kb} KB"
else
  human="$(awk -v k="$total_kb" 'BEGIN{printf "%.1f MB", k/1024}')"
fi

echo "待清理 ${#targets[@]} 项，约 ${human}："
for p in "${targets[@]}"; do
  printf '  %s\n' "$p"
done

if (( DRY )); then
  echo
  echo "[dry-run] 未做任何删除。去掉 -n 才真正清理。"
  exit 0
fi

echo
for p in "${targets[@]}"; do
  rm -rf -- "$p" && printf '已删除 %s\n' "$p"
done

echo
echo "完成。保留项未动：almond/webui/dist/（已入库）、metadata.db、config.json、cache/、_tmp/"
echo "重建：./scripts/build.sh"
exit 0
