"""抓取目标的安全边界：只允许「公网可路由的 http(s) 地址」。

看板有两个入口会让**服务器**去请求调用方给的地址（`POST /api/fetch`、`GET /api/img`），
无鉴权时等价于对外开了一个内网代理。典型命中是云主机元数据端点
`http://169.254.169.254/latest/meta-data/`（属链路本地段），其次
`http://127.0.0.1:4000/`（自己打自己）与 10/8、192.168/16 这类内网服务。

判定口径是「解析到的**每一个**地址都必须是全局单播可路由 IP」——以 `ipaddress` 的
`is_global` 为主筛（一次排掉私网、回环、链路本地、保留、未指定），比逐条列网表更不容易漏。
一个例外得单独补：`is_global` 对组播段返回 True（`224.0.0.1` 被算作「全球可达」），
所以还要再挡 `is_multicast`。

**已知不足：没有 DNS 重绑定防护。** 这里解析一次、底层发请求时再解析一次，两次之间
答案可以变（TTL=0 的恶意域名能把校验引到公网 IP、把真实请求引到内网）。彻底堵住需要把
校验后的 IP 钉住传给请求层，而 curl_cffi / scrapling 走的是域名，做不到。
因此本模块的定位是「挡住直白的内网探测」，不是「挡住蓄意重绑定攻击」。

逃生阀：`ALMOND_ALLOW_PRIVATE_URLS=1` 整体放行（内网书签、自建服务抓取用），
读取时机是调用时而非 import 时，便于测试与不改启动参数地临时放开。
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

# 与 fetcher 的超时区分开：这里只是把域名问一次，不该拖慢拒绝路径
_RESOLVE_TIMEOUT = 5


def _allow_private() -> bool:
    return (os.environ.get("ALMOND_ALLOW_PRIVATE_URLS") or "").strip() in ("1", "true", "yes")


def _iter_ips(host: str, port: int):
    """域名的全部解析结果；字面 IP 直接产出，省一次无谓的 getaddrinfo。"""
    try:
        yield ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.herror, UnicodeError):
        return
    for family, _, _, _, sockaddr in infos:
        try:
            yield ipaddress.ip_address(sockaddr[0])
        except ValueError:
            pass


def require_public_url(value, allow_private_env: bool = True) -> str | None:
    """校验可被服务器抓取的网址：合法且目标在公网返回 None，否则返回错误文案。

    解析不出任何地址时**放行**（返回 None）——那属于「域名不存在」，是抓取该报的失败，
    不是安全拦截；在这里拦成 400 会把 DNS 故障伪装成权限问题，排查更绕。
    """
    raw = (value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return "网址必须以 http:// 或 https:// 开头"
    host = parsed.hostname
    if not host:
        return "网址缺少主机名"
    if allow_private_env and _allow_private():
        return None

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for ip in _iter_ips(host, port):
        # 不能只看 is_global：它对组播段返回 True（224.0.0.1 算「全球可达」），
        # 而组播地址同样不是该让服务器去抓的目标
        if not ip.is_global or ip.is_multicast:
            return f"目标地址不在允许范围（{ip} 属内网/保留段）；需要抓内网地址请设 ALMOND_ALLOW_PRIVATE_URLS=1"
    return None
