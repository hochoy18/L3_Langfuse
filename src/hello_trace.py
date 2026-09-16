"""Lesson 0002 · SDK 最小接入 · Hello Trace.

跑这个脚本会在 Langfuse UI 的 Tracing 页面生成第一条 trace。

Usage:
    cp .env.example .env  # 填入你的 key
    uv run hello_trace.py
"""

import os

from dotenv import load_dotenv

# 仅 import 用于触发 langfuse 对 openai.resources.chat.completions.Completions.create
# 的 monkey-patch；下面自己 new 出来的 OpenAI() 实例也会被自动埋点。
from langfuse import get_client, observe
from openai import OpenAI

load_dotenv()
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL")

# MiniMax API（OpenAI 兼容协议）。显式构造客户端，把非标准前缀的 env 注入进去。
client = OpenAI(
    api_key=MINIMAX_API_KEY,
    base_url=MINIMAX_BASE_URL,
)


@observe()
def ask(question: str) -> str:
    """被 @observe 装饰的函数会变成一个 Trace，内部 LLM 调用作为子节点。"""
    resp = client.chat.completions.create(
        model="MiniMax-M3",
        messages=[{"role": "user", "content": question}],
    )
    return resp.choices[0].message.content


if __name__ == "__main__":
    answer = ask("一句话讲清楚 Langfuse 是干啥的。")
    print(answer)

    # 短脚本必加 flush，否则 trace 还没发出去进程就退了
    get_client().flush()