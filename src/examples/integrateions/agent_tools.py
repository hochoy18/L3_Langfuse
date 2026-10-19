import socket
from typing import Optional

from langchain_core.tools import tool

from src.apps.fuse import meta


def get_local_ip(family: int = socket.AF_INET) -> str:
    """获取本机对外 IP（兼容 IPv4 / IPv6 / 容器）。

    Args:
        family: socket.AF_INET (IPv4) 或 socket.AF_INET6 (IPv6)

    Returns:
        IP 字符串；失败返回 127.0.0.1
    """
    try:
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            target = ("8.8.8.8", 80) if family == socket.AF_INET else ("2001:4860:4860::8888", 80)
            s.connect(target)
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        # 回退到 hostname 解析
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


# IPv4
# ipv4 = get_local_ip(socket.AF_INET)
# print(f"IPv4: {ipv4}")