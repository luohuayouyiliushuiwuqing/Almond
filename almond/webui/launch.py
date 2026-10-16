"""启动参数：默认端口 + 「端口被占就往后跳」的窗口大小。

单独一个文件、不 import 任何东西：`scripts/_free_port.py` 只想读这两个数字，
放在 `server.py` 里会连带把 api_fetch → fetcher → Playwright 整条链拖起来。
"""

DEFAULT_PORT = 4000
PORT_SCAN = 20   # 从 --port 起最多往后试几个端口
