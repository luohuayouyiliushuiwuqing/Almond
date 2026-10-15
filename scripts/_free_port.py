#!/usr/bin/env python3
"""从 4000 起找一个本机 127.0.0.1 上空闲的 TCP 端口并打印（供 start_service.bat 选用）。"""
import socket

for p in range(4000, 4021):
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", p))
        s.close()
        print(p)
        break
    except OSError:
        pass
