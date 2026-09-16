"""Lesson 0004 · Prompt Management demo.

最小可运行 Prompt Management 例子：

1. 如果 "movie-critic" prompt 在 Langfuse 里没有带 production label 的版本，
   就创建一个 v1 并打上 production label。
2. 调两次 LLM，每次都从 Langfuse 拿 prompt，compile 后注入到 chat messages 里，
   并用 update_current_generation(prompt=...) 把 prompt 关联到当前 trace。
3. 跑完后去 http://localhost:3000 看 trace，每条 Generation 节点上会有
   "Prompt: movie-critic v1" 的徽章，点击跳到具体版本。

Usage:
    uv run python src/apps/fuse/prompt_demo.py
"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langfuse import get_client, observe
from langfuse.api.commons.errors.error import Error as LangfuseError

load_dotenv()

PROMPT_NAME = "movie-critic"


# ---------- 1. LLM（MiniMax 兼容协议） ----------


llm = ChatOpenAI(
    model="MiniMax-M3",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    temperature=0.7,
)


# ---------- 2. 幂等创建 v1 ----------


def ensure_prompt_v1() -> int:
    """如果 prompt 不存在或没有 production label，就创建一个。返回当前 production 版本号。"""
    lf = get_client()

    # 先试拿：production label 已存在则直接返回版本号
    try:
        existing = lf.get_prompt(PROMPT_NAME, label="production")
        print(f"[seed] production version already exists: v{existing.version}")
        return existing.version
    except LangfuseError:
        # 没找到 / 没 label——下面创建
        print("[seed] no production version found, creating v1")

    # 创建 v1，labels 里带 production
    lf.create_prompt(
        name=PROMPT_NAME,
        type="text",
        prompt=(
            "As a {{criticlevel}} movie critic, give a one-line take on {{movie}}."
        ),
        labels=["production"],
        config={"temperature": 0.7, "model": "MiniMax-M3"},
        commit_message="initial version",
    )
    v = lf.get_prompt(PROMPT_NAME).version
    print(f"[seed] created production v{v}")
    return v


# ---------- 3. 核心：拿 prompt + 关联到 trace ----------


@observe(as_type="generation", name="ask-critic")
def ask(criticlevel: str, movie: str) -> str:
    lf = get_client()

    # 拿带 production label 的版本（默认就拿这个）
    p = lf.get_prompt(PROMPT_NAME)

    # 替换 {{var}} 占位符
    compiled = p.compile(criticlevel=criticlevel, movie=movie)

    # ★ 关键：把 PromptClient 对象传给当前 generation——这一步让 UI 上出现
    # Prompt 徽章并链接到具体版本。如果只传 compiled 字符串，UI 不会建链接。
    lf.update_current_generation(
        prompt=p,
        model=p.config.get("model", "MiniMax-M3"),
    )

    resp = llm.invoke(
        [
            {"role": "system", "content": compiled},
            {"role": "user", "content": f"评论一下《{movie}》"},
        ]
    )

    # 把 token 用量也记到 generation 上（不写就不会显示）。
    # SDK v3 的参数名是 usage_details，不是 usage。
    usage_md = resp.usage_metadata or {}
    lf.update_current_generation(
        usage_details={
            "input_tokens": usage_md.get("input_tokens", 0),
            "output_tokens": usage_md.get("output_tokens", 0),
            "total_tokens": usage_md.get("total_tokens", 0),
        },
    )

    return resp.content


# ---------- 4. 跑 ----------


def main() -> None:
    v = ensure_prompt_v1()
    print(f">>> using movie-critic v{v}")
    print(f">>> {ask('harsh', 'Dune 2')}")
    print(f">>> {ask('harsh', 'Oppenheimer')}")
    get_client().flush()


if __name__ == "__main__":
    main()
