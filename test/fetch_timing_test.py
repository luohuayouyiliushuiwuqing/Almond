"""端到端抓取计时：直接复用内部 get_metadata，test 只调用、不改任何内部逻辑。

用法：
    python fetch_timing_test.py [URL] [N] [MODE] [--proxy ADDR]

- URL：要测的网址（默认 https://www.bilibili.com）。
- N：连续检查次数（默认 10）。
- MODE：default = 当前默认管线；og = 新增的 OG 快速模式（纯静态、跳过浏览器与 Cookie）。
  两者都只是把参数传给 get_metadata —— 没有 patch 内部函数、没有重配置 logger、
  没有换数据目录，与真实程序的调用完全一致。
- 代理获取与 webUI 的 /api/fetch 完全一致（api_fetch.fetch_one 的三步）：
    ① 拿到全局代理地址：CLI --proxy 优先；否则 detect_working_proxy() 在
       7889-7899 并行探一个转发真正可用的本机端口（与面板「检测」按钮同一条路）；
    ② resolve_proxy(url, global_proxy)：URL 规则 > 域名规则 > 全局默认，
       国内站点旁路直连（is_domestic）；
    ③ ensure_working_proxy(proxy)：健康顶替——配的端口死了就自动换端口。
  之后把 proxy / force_route 原样传给 get_metadata，与程序行为一致。
- 不再传 proxy=None：那样会跳过 ②③，且 curl_cffi 会偷偷读 shell 的
  HTTPS_PROXY 环境变量（沙箱代理 127.0.0.1:51420 对 YouTube 返回 502），
  导致测出的不是程序真实代理路径。显式传入 proxy 后 curl_cffi 用我们给的地址，
  不再回退 env。
- 使用真实 ALMOND_DATA_DIR（与程序一致）：第 1 次抓的新网址是冷启动，随后的抓取会
  因程序自身的 Cookie / 策略缓存而热身，这正是程序真实行为。
- 「走了哪条路径」不靠探针，直接看程序自己打的日志：
    Cookie 直取成功  → 默认管线的 Cookie 第 0 步
    OG 静态抓取成功  → og 模式的纯静态分支
"""

import sys
import time
from urllib.parse import urlparse

from almond.core.fetcher import get_metadata
from almond.core.proxy import detect_working_proxy, ensure_working_proxy, forced_route, resolve_proxy


def _parse_args(argv: list[str]):
    """位置参数 [URL] [N] [MODE] + 可选 --proxy ADDR。"""
    cli_proxy = None
    pos: list[str] = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--proxy":
            cli_proxy = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
            continue
        pos.append(a)
        i += 1
    url = pos[0] if len(pos) > 0 else "https://www.bilibili.com"
    n = int(pos[1]) if len(pos) > 1 else 10
    mode = pos[2] if len(pos) > 2 else "default"
    return url, n, mode, cli_proxy


def main():
    url, n, mode, cli_proxy = _parse_args(sys.argv)
    kwargs = {"detail_mode": "og"} if mode == "og" else {}

    # ── 与 webUI 完全一致的代理获取（api_fetch.fetch_one）──
    # ① 全局代理地址：手动填优先，否则在 7889-7899 自动探测（面板「检测」同源）
    global_proxy = (cli_proxy or "").strip() or (detect_working_proxy() or "")
    # ② 规则/域名/全局默认 + 国内旁路 → 实际要不要走、用哪个地址
    proxy = resolve_proxy(url, global_proxy)
    # ③ 健康顶替：配的端口死了就 detect_working_proxy 换一个
    if proxy:
        proxy = ensure_working_proxy(proxy)
    route = forced_route(url)

    via = proxy or "直连（resolve_proxy 未解析到代理 / 国内旁路）"
    print(f"URL={url}  MODE={mode}  N={n}")
    print(f"global_proxy={global_proxy or '(空)'}  →  proxy={via}  force_route={route}")
    print("（直接调用 get_metadata，代理逻辑与 /api/fetch 完全一致）")

    times: list[float] = []
    for i in range(1, n + 1):
        t0 = time.perf_counter()
        # 显式传入 proxy / force_route：与 fetchpool.submit → get_metadata 同签名
        meta = get_metadata(url, timeout=15, proxy=proxy, force_route=route, **kwargs)
        dt = time.perf_counter() - t0
        times.append(dt)
        title_disp = (meta["title"][:30] + "…") if len(meta["title"]) > 31 else meta["title"]
        print(f"  #{i:2d} {dt * 1000:8.0f}ms  ok={meta['success']}  title={title_disp!r}")

    mean = sum(times) / len(times)
    print(f"MEAN={mean * 1000:8.0f}ms")


if __name__ == "__main__":
    main()
