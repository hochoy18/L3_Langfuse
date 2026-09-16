"""Lesson 0003 · LangGraph ReAct Agent + Langfuse.

最小可运行 ReAct agent：两个工具（求和 / 当前时间），用 Langfuse CallbackHandler
把整个 graph 的多步调用一次性埋到一条 Trace 下。

Usage:
    uv run python src/apps/fuse/react_agent.py
    然后打开 http://localhost:3000 看 trace 树。
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler

from src.apps.fuse.meta import (
    AgentMeta,
    IdentityConfig,
    RuntimeMeta,
    TraceMetadata,
)

load_dotenv()


# ---------- 1. 两个简单工具 ----------


@tool
def add(a: int, b: int) -> int:
    """返回 a + b，用于测试 tool call 链路。"""
    return a + b


@tool
def now() -> str:
    """返回当前时间（ISO 格式）。"""
    return datetime.now().isoformat(timespec="seconds")


# ---------- 2. 构造 LLM + Agent ----------


llm = ChatOpenAI(
    model="MiniMax-M3",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    temperature=0,
)

agent = create_agent(llm, [add, now])


# ---------- 3. 跑一次 + 接入 Langfuse ----------


def main() -> None:
    handler = CallbackHandler()  # 全部默认配置（读 .env 里的 Langfuse key）

    question = "现在几点了？把当前时间的秒数加 100，结果是多少？"

    # 6 分类 metadata 配置：IdentityConfig 自动转 langfuse_* 前缀
    meta = TraceMetadata(
        identity=IdentityConfig(
            session_id="demo-001",
            user_id="demo-user",
            tags=["lesson-0003", "react"],
            trace_name="react-agent-demo",
        ),
        agent=AgentMeta(
            agent_name="react-demo",
            agent_version="0.1.0",
            agent_strategy="react",
        ),
        runtime=RuntimeMeta(
            environment=os.getenv("ENV", "dev"),
            input_source="script",
        ),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config=meta.to_langchain_config(callbacks=[handler]),
    )

    # 最后一条 message 是 AIMessage
    print(">>>", result["messages"][-1].content)
    print("trace id =", handler.last_trace_id)


if __name__ == "__main__":
    main()