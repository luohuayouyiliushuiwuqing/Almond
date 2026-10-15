# Almond — 网页元数据抓取看板

抓取网页元数据（标题、缩略图、favicon、结构化详情字段），以 Raindrop 风格的卡片看板展示。
基于 [Scrapling](https://github.com/D4Vinci/Scrapling)（TLS 指纹伪装 + 三级降级抓取），数据存 SQLite，界面为本地 Web 应用。

## 功能

- **三级降级抓取**：`Fetcher`（TLS 指纹）→ `DynamicFetcher`（JS 渲染）→ `StealthyFetcher`（反 Cloudflare），配代理失败自动回退直连
- **元数据提取**：标题按 `og:title → twitter:title → <title>` 优先级；缩略图 `og:image`；favicon `link[rel*="icon"]`，相对路径自动补全
- **详情字段解析**：抓取时顺带解析 `.space-y-2 > *` 中的「标签: 值」结构（发行日期、番号、类型链接等），在看板「详情」弹层展示
- **Raindrop 看板**：卡片网格（缩略图在上、标题在下）、左侧栏按域名/标签筛选、搜索过滤、添加 / 重命名 / 重新抓取 / 删除
- **导入 / 导出**：TXT 与 Raindrop HTML 导入——HTML 走**快照入库**（不联网、5000 条秒级完成），详情字段在打开时按需补抓；当前可见记录可导出 TXT / HTML
- **域名分组与代理映射**：按根域名分组（零散域名收进「其他」），URL 通配符规则 + 域名级开关只表达「要不要走代理」，地址统一填一次；走代理的域名在侧栏带「代理」徽标
- **服务端图片代理 + 本地缓存**：缩略图与 favicon 经本地代理加载（避免浏览器直连外站被重置），结果落盘缓存，二次加载不再走网络
- **SQLite 存储**：`metadata.db`（存放位置可在界面「存储」面板里点选迁移），合并规则保证抓取失败不覆盖已有数据和手动补充的标题
- **默认只对本机开放**：绑定 `127.0.0.1:4000`；对外监听必须配 token，否则拒绝启动。鉴权双凭证——浏览器换会话 Cookie、脚本与插件带 `X-Token` 头；服务端出网目标一律过滤内网 / 保留段（含云元数据地址）

## 目录结构

```
Almond/
├── pyproject.toml         # 打包配置：依赖来源、控制台入口（只剩 almond-webui）
├── requirements.txt       # 全量 pinned 依赖（同时是 pyproject 的依赖来源）
├── frontend/              # 前端源码（React 18 + Vite，需构建）
│   ├── index.html         # SPA 入口 HTML（标题等）
│   ├── vite.config.js     # Vite 配置：base '/app/'，产物直出 almond/webui/dist/
│   ├── package.json       # 前端依赖与脚本（dev / build / preview）
│   └── src/               # 源码：App.jsx + 组件 + store.js + 样式
├── almond/                # Python 包（绝对 import，无 sys.path hack）
│   ├── core/              # 与界面无关的核心逻辑
│   │   ├── fetcher.py     # 核心：三级降级抓取 + 元数据/详情提取
│   │   ├── fetchpool.py   # 抓取子进程池：抓取不占 Web 进程，切库即硬杀（含浏览器进程树）
│   │   ├── confirm.py     # 方案确认：自动抓取前先试出每个域名的最优抓取方案，没方案整组跳过
│   │   ├── fetch_hints.py · hints_strategy.py · hints_cookie.py
│   │   │                  # 经验缓存本体 / 策略成绩册 / Cookie 层
│   │   ├── proxy.py       # 代理唯一入口：规则匹配 + 端口探测顶替 + 定时刷新
│   │   ├── urlguard.py    # 出网地址策略：拦掉内网 / 保留 / 组播段（SSRF 兜底，抓取前最后一道）
│   │   ├── storage.py · db.py         # 存储目录与纪元 / SQLite 连接与迁移
│   │   ├── records.py · domains.py    # 记录标签规则 CRUD / 域名配置与重置
│   │   └── fields.py · hostutil.py · log.py
│   │                      # 「标签: 值」解析 / 根域名与国内判定 / 日志
│   └── webui/             # 唯一界面后端：Raindrop 看板（默认 127.0.0.1:4000）
│       ├── server.py      # 路由表合并 + 鉴权闸 + 薄 Handler + 启动入口
│       ├── auth.py        # token 鉴权 + 会话 Cookie（`?token=` 换 Cookie / `X-Token` 头）
│       ├── api_{fetch,records,domains,proxy,storage}.py   # 按资源的 REST 处理器
│       ├── api_util.py    # 参数校验与 JSON 响应助手
│       ├── staticfiles.py · imgproxy.py · fsbrowse.py
│       │                  # 静态解析 / 图片代抓 / 目录浏览（都不碰 socket）
│       └── dist/          # Vite 构建产物（被后端直接 serve，已纳入版本控制）
├── tests/                 # 独立测试脚本（无 pytest：unittest 用例 + 抓取演练脚本）
├── scripts/               # stop_service.{sh,ps1,bat}：停服务进程并释放端口
├── extension/             # 浏览器插件（Chrome/Edge MV3）：当前页 / 标签页一键收藏进看板，加载即用
├── docs/                  # git 子模块（→ AlmondMD）：CHANGE.md 变更记录 · TODO.md 待办 · ROADMAP.md 功能方向 · CLAUDE.md 使用指引 · ARCHITECTURE.md 架构
├── metadata.db            # SQLite 数据库（运行时生成，已 gitignore，位置可在界面迁移）
└── config.json            # 记录当前存储目录（运行时生成，已 gitignore）
```

## 环境准备

```bash
# 1. conda 环境（本项目使用 almond）
conda activate almond

# 2. 安装依赖（可加清华源）
pip install -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# 3. 下载浏览器内核（DynamicFetcher / StealthyFetcher 需要）
patchright install chromium

# 4. 安装本项目（editable；依赖已在第 2 步装好，故 --no-deps 跳过解析，
#    --no-build-isolation --no-index 全程不联网）
pip install -e . --no-deps --no-build-isolation --no-index
```

## 前端构建（React + Vite）

前端已从原生 ES 模块（无构建步骤）迁移到 **React 18 + Vite**，源码在 `frontend/`，
构建后由后端 `almond-webui` / `python -m almond.webui.server` 直接托管，改完前端必须重新构建。

```bash
cd frontend

# 1. 首次安装前端依赖（Node ≥ 18）
npm install

# 2. 开发模式（热更新，独立 Vite dev server，默认 http://localhost:5173）
#    dev 阶段用 Vite 反代 /api 免 CORS；后端跑在别的端口时用环境变量指定：
VITE_API_TARGET=http://127.0.0.1:8888 npm run dev

# 3. 构建生产包（输出到 almond/webui/dist/，后端直接 serve）
npm run build
```

- 构建出的 SPA 由后端托管：`/` 返回 `dist/index.html`，静态资源挂在 `/app/*`（见 `vite.config.js` 的 `base: '/app/'`）；未知且非 `/api/*` 的路径回落到 SPA 入口，支持 `#/domain/xxx` 刷新 / 分享链接直达。
- 改完 `frontend/src/` 后需 `npm run build` 才能看到效果；后端每次请求从磁盘读 `dist/`，**无需重启后端**。
- 前端运行时不依赖任何 npm 全局包；构建产物已纳入版本控制（`.gitignore` 放行 `almond/webui/dist/`）。

## 使用

### 看板（主界面）

```bash
almond-webui                              # 默认 http://127.0.0.1:4000/（只监听本机，此时无需 token）
almond-webui --port 8000                  # 自定义端口
almond-webui --host 0.0.0.0 --token <值>  # 对外监听：token 必填，否则拒绝启动（退出码 2）
ALMOND_TOKEN=<值> almond-webui --host 0.0.0.0   # 同一 token 的推荐给法——不进 ps 与 shell history

# 免安装方式（必须在仓库根目录执行）
python -m almond.webui.server [--host 127.0.0.1] [--port 4000] [--token <值>]
```

- 启用了 token 时，第一次访问把 token 挂在地址后即可：`http://<地址>:4000/app/?token=<值>`——服务端换成 30 天的会话 Cookie 并把 token 从地址栏带走（`302` 到 `/app/`）。之后 `curl` / 脚本带 `X-Token: <值>` 头即可。

- 顶部粘贴 URL 回车「添加」；右侧搜索框过滤；`☰` 收起 / 展开左侧栏
- 左侧栏按**域名**与**标签**筛选看板：条目数 <10 的零散域名收进「其他」，走代理的域名带「代理」徽标
- 卡片悬停出现两处操作——缩略图右上角 **详情 / 重新抓取**，底部 **重命名 / 标签 / 删除**
- **多选与批量**：卡片左上角的复选框（常驻、半透明）勾上任意一张后，看板底部浮出操作条——
  **全选 / 全不选 / 反选**（按**当前可见**记录，跟随搜索与筛选）、**标签 / 重新抓取 / 删除 / 导出**
  （作用于整个选中集合）。重新抓取进补抓队列（5 并发、可暂停、按钮报进度），删除只需一次确认；
  选择跨筛选保留，筛选切换不会丢
- **导入** 支持 TXT 与 Raindrop HTML：链接**先全部入库**（HTML 走快照，秒级且不联网），随后**自动补抓**标题与详情；**导出** 按当前可见记录导出 TXT / HTML
- **自动补抓** 打开页面即把库里「从未抓取过」的记录（灰点）5 条并发抓完，导入产生的新链接接着排；页面开着期间，别处（另一标签页、浏览器插件、接口导入）新进库的链接每 10 秒被发现一次，同样会自动开抓；**先确认、后抓取**——每个域名先由「方案确认」阶段挑一条真实网址试出最优抓取方案（直连/代理/JS 多路尝试），**有方案的域名才放行整组抓取**；确认不通、或确认过但方案已失效的域名**整组跳过**（实测面板标「无方案」），人工强制补抓不受此限；**每轮开跑前先重新探测一次本机代理端口（7889-7899，端口探开后还会验证转发真的可用——只开着不转发的坏端口不会被填回来）**，代理刚起来或换过端口也能拿到最新地址（在「代理」面板点过「保存」的地址——包括清空——不会被探测覆盖）。工具栏 **补抓** 按钮**常驻不隐藏**，没活干时置灰（禁用）；运行中显示进度 `补抓 x/N`，点一下暂停、再点继续（刷新后仍记住暂停）。**空闲时点它 = 强制补抓**——从未抓过的和抓取失败的一起重跑；自动补抓则**不碰失败记录**（留红点），也可悬停卡片手动「重新抓取」。没待抓也没失败时按钮禁用（`补抓` 置灰）
- **代理** 打开代理面板：填全局代理地址（运行中改也行，自下一条抓取起生效）、配 URL 通配符规则与域名级开关；打开页面自动探测本机 7889-7899 端口并**验证转发可用**；抓取时发现当前端口转发不通，会自动在区间里找可用端口顶替并回写面板，找不到才降级直连
- **存储** 打开存储面板：查看并切换数据库、图片缓存的存放目录（**不迁移**：旧数据留在原地、新目录从零开始；切换瞬间旧看板即清空、后台在途抓取**连同浏览器进程树一并硬停**，随后并行载入新目录；切过的位置留在「历史位置」，可一键回切）
- **域名管理 / 域名重置** 侧栏条目 hover「编辑」打开域名弹窗：改显示名、代理开关，或把失效域名（含其子域名）**整体重置**为新域名——完成后状态栏报出「共 N 条，成功 N，重复 N，失败 N，改写规则 N 条」
- **域名子页面**：侧栏域名行**点名字仍是筛选**，行**左「编辑」**开弹窗、行**右「详情」**进该域名的子页（地址栏变 `#/domain/xxx`，刷新、浏览器后退、直接分享链接都能用）；零散域名（侧栏不显示的）从「代理」面板的域名列表进入。子页里是**该域名专属的列表清单**——域头给统计与设置（别名 / 代理 / 自动补抓开关 / 封面来源 / 按站点详情选择器 / 域名重置），工具栏的**排序、展示字段、标签筛选按域名记住**，下次进这个域名自动套用；列表行可勾选，直接复用看板的全选 / 反选 / 标签 / 重新抓取 / 删除 / 导出
- 点缩略图或标题在新标签打开链接

## API（webui）

除 `/api/login` 外，全部 `/api/*` 都要凭证（未配 token 时不设闸）。缺凭证或凭证错回 **401** `{ok: false, unauthorized: true}`：浏览器由前端弹窗引导补 token，脚本 / 插件带 `X-Token` 头。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/login?token=…` | **唯一免鉴权接口**：token 正确则下发会话 Cookie 并 302 回 `/app/`（把 token 从地址栏、浏览器历史与反代访问日志里带走）；错误回 401。Cookie 为 `HttpOnly` + `SameSite=Lax`、30 天；经反代终结 TLS 时（`X-Forwarded-Proto: https`）追加 `Secure` |
| POST | `/api/logout` | 清会话 Cookie。仍需先过鉴权，免得被当成免费的「这个 token 对不对」探针。**注意**：Cookie 值是 `sha256("almond-session:" + token)`，服务端不保存会话状态，所以登出只对当前浏览器生效——要真正作废一个已泄漏的 Cookie，只能换 token 重启 |
| GET | `/api/records` | 全部记录（含标签与 `fetched` 快照标记）+ `epoch`（当前存储纪元，抓取请求要带上） |
| POST | `/api/fetch` | `{url, proxy, epoch}` **异步提交**，立即回 `{ok: true, job_id}`；结果由 `GET /api/fetch/status?job_id=…` 收（前端延后 1s 开轮询、每 500ms 一次、5 分钟封顶）。抓取跑在**子进程**（切库即硬杀，不占 Web 进程），按域名配置应用 `detail_selector` / `detail_mode` / `cover`；**完成后的那份响应**才带合并入库的记录与 `success`（**本次**抓取是否成功——失败按合并规则不覆盖已有内容，`record.success` 可能仍是旧值）；`epoch` 与当前纪元不符（切库后才到达的旧请求）直接作废回 `stale`，一个包都不发；本机代理端口转发不通时自动顶替为可用端口，顶替结果在**提交响应**里就经 `proxy_swapped` 回传，不必等轮询 |
| GET | `/api/fetch/status?job_id=…` | 异步抓取结果查询（供前端轮询）：**200** 任务完成、返回与 `/api/fetch` 同格式的完整结果；**204** 仍在跑、无 body；**404** `job_id` 无效或已被切库清掉；结果属于旧纪元的回 `stale: true` 并作废 |
| POST | `/api/records/quick` | `{items:[{url,title,thumbnail,favicon,tags}]}` 快照批量入库，不联网 |
| GET | `/api/records/pending` | 未抓取记录（`fetched=0`）的 URL 列表——补抓队列的轮询源，页面开着时发现别处新进库的链接 |
| PATCH | `/api/record` | `{url, title}` 重命名 |
| DELETE | `/api/record?url=…` | 删除记录 |
| GET | `/api/img?src=…&proxy=…` | 服务端代抓图片（缩略图/favicon 用，命中本地缓存不走网络） |
| GET | `/api/domains` | 域名列表：`display_name`、`need_proxy` 三态（null 跟随全局 / 1 用代理 / 0 直连），以及每域名配置 `prefs`（显示偏好 JSON）、`auto_fetch`（自动补抓开关，null 跟随）、`detail_selector`（按站点详情选择器）、`detail_mode`（详情提取形态：`''` 「标签: 值」行 / `'tags'` 扁平标签链 / `'og'` OG 快速模式）、`cover`（封面开关）、**`tested`（系统实测：可达性结论 + `strategy` 方案 id + `plan` 是否有可用方案 + `confirm` 确认结论，只读）** |
| POST | `/api/confirm` | `{domains?, proxy, epoch}` **方案确认阶段**：给没有方案的域名各挑一条真实网址试抓一次，成功学出方案并入库、失败写持久结论；返回 `{ready, confirmed, failed, skipped, transient, aborted}`（无方案的域名由前端整组跳过）；`auto_fetch=0` 的域名不参与 |
| PATCH | `/api/domain` | `{name, display_name?, need_proxy?, prefs?, auto_fetch?, detail_selector?, detail_mode?, cover?}` 更新域名设置（只改给到的字段） |
| POST | `/api/domain/replace` | `{old, new}` 域名重置：原域名（含子域名）在库里的全部引用整体换成新域名；返回 `total/records/merged/failed/rules`（命中 / 成功 / 重复 / 失败 / 改写规则） |
| GET | `/api/tags` | 标签列表 |
| POST | `/api/tag` | `{name}` 新建标签 |
| DELETE | `/api/tag?id=…` | 删除标签 |
| POST | `/api/record/tag` | `{url, tag_id}` 给记录打标签 |
| DELETE | `/api/record/tag?url=…&tag_id=…` | 移除记录标签 |
| GET | `/api/proxy-rules` | URL 通配符规则列表 |
| POST | `/api/proxy-rule` | `{pattern, need_proxy}` 新增 / 更新规则 |
| DELETE | `/api/proxy-rule?id=…` | 删除规则 |
| GET | `/api/proxy-domains` | 最终会走代理的根域名（侧栏徽标数据源） |
| POST | `/api/proxy-detect` | 探测本机 7889-7899 代理端口并**验证转发可用**，返回可用地址或 `null`（只开着不转发的坏端口不算） |
| GET | `/api/proxy-current` | 后台定时线程最近一次探测的结论 `{proxy}`；**只读内存缓存、不发起探测**，供前端被动跟随（主动检测走上面的 `/api/proxy-detect`） |
| GET | `/api/storage-dir` | 当前存储目录 / 数据库 / 缓存路径 |
| POST | `/api/storage-dir` | `{path}` 切换存储目录（**不迁移**：旧数据留在原地、新目录从零开始；响应 `records` = 新目录记录数，`epoch` +1 作废在途抓取） |
| DELETE | `/api/storage-history?path=…` | 从存储目录的历史位置里移除一条（当前目录不可移除） |
| GET | `/api/fs/list?path=…` | 目录浏览（空 path 返回盘符 / 根，供界面点选目录） |

## 说明

- **代理**：映射只表达「要不要走代理」，地址统一填在「代理」面板的全局代理里（Clash 等，默认 `http://127.0.0.1:7897`，打开页面自动探测本机 7889-7899 并验证转发）。优先级 **URL 模式规则 > 域名规则 > 全局默认**，命中即为强制值（含「强制直连」）；国内域名自动跳过代理；当前端口转发不通时先自动顶替成本机可用端口，找不到才回退直连再试整条降级链
- **合并规则**：按 URL 去重；抓取失败保留原记录；页面无标题时不覆盖手动补充的标题
- **数据文件**：本地私有数据（`metadata.db`、图片缓存 `cache/img/` 等）统一放在**存储目录**——默认仓库根（已 gitignore），可在界面「存储」面板点选目录切换（不迁移，立即生效）；位置记录在仓库根 `config.json`（已 gitignore）。非 editable 安装可用环境变量 `ALMOND_DATA_DIR` 指定基础目录
- **入口约定**：只支持 `almond-webui` 或 `python -m almond.webui.server`（后者需在仓库根目录执行）；不要直接 `python almond/webui/server.py`，它依赖 `sys.path`，行为随安装状态变化
- **监听地址**：看板默认 `127.0.0.1:4000`，只对本机可见；`--host` / `--port` 可覆盖。**绑非回环地址又没配 token 一律拒绝启动**（退出码 2）——这类启动没有「先跑起来再说」的余地
- **鉴权**：token 由 `--token` 或环境变量 `ALMOND_TOKEN` 给出（推荐后者，`--token` 的值会进 `ps` 与 shell history）。两种凭证：浏览器用**会话 Cookie**（`GET /api/login?token=…`，或在任意页面地址后挂 `?token=` 换 Cookie），脚本与浏览器插件用 **`X-Token` 头**——插件 popup 的源是 `chrome-extension://…`，跨站请求带不动 `SameSite=Lax` 的 Cookie，只有显式头能过闸。**未配 token 时完全不设闸**，回环监听下这是有意的省事。经 Caddy / nginx 反代时，`Secure` 标志只看 `X-Forwarded-Proto`，不看连接本身（反代终结 TLS 时后端拿到的是明文连接）
- **SSRF 与出网策略**：`POST /api/fetch`、`POST /api/confirm` 与 `GET /api/img` 会让**服务器**去请求调用方给的任意地址，所以统一过 `almond/core/urlguard.py`：非 http(s)、缺主机名、解析后落在回环 / 私网 / 链路本地 / 保留 / 组播段的地址一律拒绝，`169.254.169.254`（云元数据）在内。`/api/fetch` 与 `/api/img` 直接调它回 **400** 并说明拦下原因；`/api/confirm` 以及一切别的路径（含 `confirm.py` 这类直接从库里取 URL 灌进抓取池的）由 `fetcher.fetch_page()` 开头那道**兜底**拦下——写日志、不发起抓取。确实要抓内网服务时设 `ALMOND_ALLOW_PRIVATE_URLS=1` 放行。**已知不足**：策略在「解析 DNS」之后判定，而抓取在自己内部又解析一次，因此理论上存在 DNS 重绑定窗口——本策略挡的是直白的内网探测，不是蓄意的重绑定攻击；上公网仍应在反代 / 安全组后
- **其余对外风险**（token 只挡住无凭证者，不区分持凭证的人）：`GET /api/fs/list` 可列出服务器任意目录、`POST /api/storage-dir` 可把数据库指向别处；云主机上还要同时放行安全组与 `firewalld`/`ufw` 的对应端口，否则外部访问会超时
- **调试抓取链**：抓取日志会打印到启动命令所在的终端
