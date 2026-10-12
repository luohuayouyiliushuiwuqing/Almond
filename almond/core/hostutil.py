"""主机名分类：根域名提取与国内站点判定（跨层共用的纯函数）。

原先 `_root_domain` 埋在 records、`_is_domestic` 埋在 fetcher，
confirm / imgproxy / 测试脚本全在**私有伸手** import 带下划线的名字——
统一到这里，对外只用公开名 `root_domain` / `is_domestic`。
"""

from urllib.parse import urlparse

# 多段公共后缀：co.uk / com.cn / com.au … —— 这类要连前一段一起取，
# 否则 bar.co.uk 会被截成 co.uk，导致分组错乱、域名代理规则对不上。
# 必须与前端 rootDomain()（js/state.js）保持同一份列表，否则两边分组口径不一致。
MULTI_TLDS = (
    ".co.uk", ".org.uk", ".ac.uk", ".gov.uk", ".me.uk",
    ".com.cn", ".net.cn", ".org.cn", ".gov.cn", ".edu.cn",
    ".co.jp", ".ne.jp", ".or.jp", ".ac.jp",
    ".com.au", ".net.au", ".org.au",
    ".co.nz", ".com.hk", ".com.tw", ".com.sg",
    ".com.br", ".com.mx", ".co.kr", ".co.in", ".com.ar",
)

# 国内域名后缀（.cn 及其二级后缀）
_DOMESTIC_SUFFIXES = (".cn", ".com.cn", ".net.cn", ".org.cn")
# 常见国内域名（不含 .cn 后缀，需显式列出）
_DOMESTIC_DOMAINS: set[str] = {
    "baidu.com", "bilibili.com", "zhihu.com", "weibo.com", "douyin.com",
    "xiaohongshu.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "qq.com", "weixin.qq.com", "163.com", "126.com", "sina.com", "sohu.com",
    "csdn.net", "cnblogs.com", "jianshu.com", "juejin.cn",
    "aliyun.com", "tencent.com", "huawei.com", "xiaomi.com", "mi.com",
    "xiaomimimo.com", "deepseek.com",
    "apple.com", "microsoft.com", "google.com.hk",
    "feishu.cn", "dingtalk.com", "yuque.com",
    "hdslb.com", "127.net", "netease.com", "kimi.com",
}


def root_domain(url: str) -> str:
    """从 URL 提取可注册域名（处理多段公共后缀）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    for tld in MULTI_TLDS:
        if host.endswith(tld):
            head = host[: -len(tld)]
            return f"{head.rsplit('.', 1)[-1]}{tld}" if head else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_domestic(url: str) -> bool:
    """判断 URL 是否为国内网站（不走代理）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if any(host.endswith(s) for s in _DOMESTIC_SUFFIXES):
        return True
    # 子域名匹配：mimo.mi.com → 检查 mi.com
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in _DOMESTIC_DOMAINS:
            return True
    return False
