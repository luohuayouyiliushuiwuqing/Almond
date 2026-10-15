# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Deep manual: [`docs/CLAUDE.md`](docs/CLAUDE.md)** (inside the `docs/` git submodule) — read the
section for a subsystem before changing it. Fetchpool generation/kill semantics, storage-epoch TOCTOU
guards, the frontend circular-import rule, the strategy / cookie / confirm layers and the per-module
frontend table all live there and are easy to break silently. This root file holds only what every
session needs up front: conventions, commands, and the architecture map.

## Project

Almond — a local web bookmark board: scrapes page metadata (title / thumbnail / favicon / "label: value"
detail fields) and shows it as a Raindrop-style card grid. A Python stdlib HTTP server (no web framework)
exposes SQLite-backed REST APIs; the frontend is **React 18 + Vite** (`frontend/`, built into
`almond/webui/dist/` and served by that same backend), plus a Chrome MV3 extension (`extension/`) that
posts to the snapshot-import API. UI strings, code comments and commit messages are written in
**Chinese** — match that convention.

## Non-negotiable conventions

1. **CHANGE.md entry per batch.** Every batch of modifications (counted by *change*, not by commit —
   uncommitted work still counts) is appended to `docs/CHANGE.md`, **newest first**, in this exact shape:

   ```markdown
   ## 2026-10-08 09:28:17 +0800
   - **用户**: haijie yin
   - **系统**: Ubuntu 20.04.6 LTS · Linux 5.4.0-21-generic x86_64 · igs-Y
   - **内容**: one-line summary of this batch
     - sub-item: what exactly changed
   ```

   Field sources: time `date "+%Y-%m-%d %H:%M:%S %z"`; 用户 `git config user.name`;
   系统 `/etc/os-release` 的 `NAME`+`VERSION` · `uname -srm` · `hostname`.
2. **TODO.md.** Pending work is `docs/TODO.md` — numbered, priority order, one checkbox per item.
   Finishing one: tick it and move it under 「已完成」; that move *and* the underlying change both
   still get a CHANGE.md entry.
3. **Docs are a two-repo commit.** `docs/` is a git submodule (→ AlmondMD): commit *inside* `docs/`
   first, then commit the bumped pointer in the parent (`git add docs && git commit`). `README.md`
   and this file are the only files that stay in the parent repo.
4. **Entry points.** Only `almond-webui` / `python -m almond.webui.server` (the latter runs from the
   repo root). Never document or recommend `python almond/webui/server.py` — absolute imports
   need `almond` on `sys.path`, which a direct script run does not provide.

## Commands

```bash
conda activate almond

# one-time setup
pip install -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
patchright install chromium            # DynamicFetcher / StealthyFetcher need the browser kernel
pip install -e . --no-deps --no-build-isolation --no-index   # console entry points, offline
npm --prefix frontend install          # React + Vite toolchain

# frontend — the backend serves almond/webui/dist/, so any frontend change must be rebuilt
npm --prefix frontend run dev               # :5173, /api proxied to the Python backend
npm --prefix frontend run build             # → almond/webui/dist/

# run
almond-webui                                  # 127.0.0.1:4000, no auth gate (loopback only)
almond-webui --host 0.0.0.0 --token <v>       # public bind REQUIRES a token, else exit code 2
ALMOND_TOKEN=<v> almond-webui --host 0.0.0.0  # preferred — token stays out of `ps` / shell history
python -m almond.webui.server [...]           # same, without installing (from repo root)

# stop the board, including fetched browser process trees (port 4000)
./scripts/stop_service.sh                     # Windows twins: stop_service.ps1 / stop_service.bat

# the project's only test harnesses (there is no pytest suite) — one script per feature
python -m unittest discover -s tests          # pure-logic cases, no network
python tests/scrapling_test.py [urls…] [--proxy … | --no-proxy] [--timeout 15] \
        [--no-browser] [--full] [-v] [--cookie STR]     # fetch stack + hints cache drill
python tests/cookie_test.py <url> [--fresh | --cookie-json JSON|FILE] \
        [--show-cookie] [--proxy …] [--timeout 20]      # cookie harvest → cookie-direct check
```

⚠ patchright must match the playwright chromium revision pinned in `requirements.txt` (1.62.x →
chromium-1234). Mismatch fills the fetch log with `Executable doesn't exist at …/chromium-12xx/…`
and tiers 2–3 silently die (only `Fetcher` survives). Newer patchright refuses to install on
Ubuntu 20.04 at all, so check the playwright pin before any version bump.

No linter or formatter is configured. Offline checks before opening a browser:

```bash
python -m py_compile almond/core/*.py almond/webui/*.py
python -m unittest discover -s tests                 # logic-level regressions, no network
npm --prefix frontend run build                      # JSX + whole import graph must resolve
```

End-to-end behaviour (sidebar overflow, two-phase import, in-place card patching, …) is browser-only:
drive it with Playwright against a server pointed at a throwaway `ALMOND_DATA_DIR` so the real
`metadata.db` is never touched.

## Architecture

Each layer talks only to the one directly below it (full detail in `docs/CLAUDE.md` —
its「分层规范」section is binding):

```
frontend/src/               React 18 SPA，Vite 构建 → almond/webui/dist/，后端挂在 /app
  store.js  唯一可变状态 + 全部业务动作（useSyncExternalStore + version 计数驱动重渲）
  api.js    统一请求层（fetch() 只出现在这里：epoch 注入 / 中止登记 / proxy_swapped / fetchJob 轮询 / 401 引导取 token）
  ui.js     模态总线（openModal(type, payload)，App 统一渲染弹层）
    ↓ HTTP / REST
almond/webui/server.py      薄 Handler：路由表合并 + 鉴权闸 + dispatch + 兜底
almond/webui/auth.py        token 校验 + 会话 Cookie（Cookie 给浏览器、X-Token 给脚本/插件）+ 登录登出
almond/webui/api_*.py       按资源 REST 处理器（fetch/records/domains/proxy/storage）
almond/webui/{api_util,staticfiles,imgproxy,fsbrowse}.py  校验助手 / 静态 / 图片 / 目录
    ↓
almond/core/fetcher.py      三级降级抓取 + 元数据/详情提取（force_route：显式路由约束策略层）
almond/core/fetchpool.py    抓取子进程池：/api/fetch 的执行器，切库即硬杀进程树
almond/core/confirm.py      方案确认：自动抓取前先试出每个域名的可用方案
almond/core/fetch_hints.py  经验缓存本体：JSON 存取 + 效应通道 + 纪元门 + 可达性 + plan/confirm
almond/core/hints_strategy.py 策略成绩册 · hints_cookie.py Cookie 层
almond/core/proxy.py        代理唯一入口：规则匹配 + resolve_proxy/forced_route + 转发健康 + 端口顶替 + 定时刷新
almond/core/urlguard.py     出网地址策略（SSRF）：require_public_url，fetch_page 与 webui 三条触网入口共用
almond/core/storage.py      存储目录 / 纪元 / SWITCH_LOCK · db.py SQLite 连接+迁移
almond/core/records.py      记录/标签/规则 CRUD · domains.py 域名配置+重置
almond/core/hostutil.py     根域名 + 国内判定 · fields.py「标签: 值」· log.py 日志
```

`almond/webui/` is the only server in the tree. `extension/` is a standalone MV3 extension — zero
server changes, it posts to `POST /api/records/quick`; see `extension/README.md` before touching it
(CORS/absolute-URL pitfalls are listed there).

Invariants that must survive any edit:

- **Merge rule** (`records.upsert_record`, keyed on `url`): a `success: False` fetch never overwrites
  row content but *does* set `fetched = 1` ("尝试过"); an empty incoming title never clobbers an
  existing (possibly manual) title.
- **Storage epoch**: `/api/fetch` carries `state.storageEpoch`; a storage-dir switch bumps
  `_STORAGE_EPOCH`, hard-kills the fetchpool children (whole browser tree), and writes go through
  `upsert_record_if_epoch()` so late results never land in the new db.
- **Frontend seams**: 可变状态只有 `store.js` 一处，组件经 `useStore()` 读、调 `store.js` 导出的动作写，
  不在组件里另存看板数据。`api.js ⇄ store.js` 是有意相互 import——双方都只在函数调用期取对方导出，
  改成模块求值期解引用会拿到 undefined。所有 HTTP 只经 `api.js`（含批量删除——它曾是 `SelectBar.jsx`
  里的裸 `fetch` 循环，已收进 `store.js` 的 `removeSelected()`）：绕开 `api.js` 就同时绕开了
  epoch 注入、`abortAll()` 与 401 引导。
- **`requirements.txt` is a build input** (pyproject dynamic deps): only full-line `#` comments and
  `name==ver` lines — no inline comments, no `-i`/`-r` option lines, or the build breaks.
- **Auth is a bind-time property, not a per-peer check**: `--host` non-loopback without a token refuses
  to start; once a token is set, `server._dispatch` gates every `/api/*` except `/api/login` (behind a
  reverse proxy every peer looks like 127.0.0.1, so per-request peer checks would be meaningless).
  Cookies authenticate the browser (`<img src>` cannot carry a header); `X-Token` authenticates scripts
  and the MV3 popup, whose `chrome-extension://` origin cannot carry a `SameSite=Lax` cookie. Emit the
  401 **after** `read_json()` drains the body or keep-alive connections desync.
- **SSRF policy lives in core** (`almond/core/urlguard.py`): `almond/core/` must never import
  `almond/webui/`, so the check sits next to `proxy.py` and is called both by the webui handlers (crisp
  400s) and by `fetcher.fetch_page()` as the backstop — `confirm.py` feeds DB-sourced URLs straight into
  the fetchpool, bypassing the handlers. It is not a rebinding defense: the IP is checked at resolve
  time, then resolved again inside the fetch.

## Storage layout

All private data (`metadata.db`, image cache `cache/img/`, fetch experience cache
`cache/fetch_hints.json`) lives in a single *storage dir* — default the repo root (gitignored),
switchable from the UI 「存储」 panel with **no migration** (old dir untouched, new dir starts
empty). `config.json` (repo root, gitignored) records the current dir + history; `ALMOND_DATA_DIR`
relocates the *base dir* only. Deleting `cache/fetch_hints.json` resets all learned hints / cookies /
strategies for that storage dir.
