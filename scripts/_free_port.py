#!/usr/bin/env python3
"""从默认端口起找一个 127.0.0.1 上空闲的 TCP 端口并打印（供 start_service.bat 选用）。

区间不写死：读 almond/webui/launch.py 的 DEFAULT_PORT + PORT_SCAN，和服务端 _bind
的跳转窗口同一个源。两边各自写死会差一（旧版这里是 4000-4020 共 21 个，服务端只试
20 个），脚本说 4020 空着、服务端却根本不会试到那儿。
全被占用时不输出——bat 回退到 4000，最终由 _bind 报「N 个端口全被占用，放弃启动」。
"""
import socket
import sys
from pathlib import Path

# 本脚本不在包内，直接把仓库根挂上，保证读的是自己这棵树的常量
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from almond.webui.launch import DEFAULT_PORT, PORT_SCAN  # noqa: E402

for p in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN):
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", p))
        s.close()
        print(p)
        break
    except OSError:
        pass
