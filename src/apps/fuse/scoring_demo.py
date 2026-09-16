"""Lesson 0005-02 · Score 三种 Evaluator 实现 demo.

演示 3 件事：
1. Code Evaluator：output_present / response_length（确定性规则）
2. LLM-as-a-Judge：helpfulness（语义判断）
3. 把所有 score 挂到当前 trace

复用 lesson 0004 的 movie-critic prompt。

Usage:
    uv run python src/apps/fuse/scoring_demo.py

预期输出：
```text
    >> output: "I love Dune 2 because of its..."
    >> rule-output-present: 1.0 (present)
    >> rule-response-length: 256 chars
    >> judge-helpfulness: 0.75 (correct + concise)
```
"""

import json
import os
import re

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


# ---------- 2. Code Evaluators（纯函数） ----------


def rule_output_present(output: str) -> tuple[float, str]:
    """规则 1：output 是否非空。"""
    present = bool(output and str(output).strip())
    return (1.0 if present else 0.0), "present" if present else "missing or empty"


def rule_response_length(output: str, min_chars: int = 10) -> tuple[float, str]:
    """规则 2：长度是否足够（返回实际长度作为 NUMERIC score）。"""
    n = len(str(output or ""))
    ok = n >= min_chars
    return (
        float(n),
        f"len={n} ({'ok' if ok else 'too_short'})",
    )


# ---------- 3. LLM-as-a-Judge（judge_helpfulness） ----------


JUDGE_PROMPT = """You are evaluating an AI assistant's response for helpfulness.

Score on a 1-5 scale:
- 5 = perfect, addresses the question completely and concisely
- 4 = good, addresses with minor issues
- 3 = acceptable, partially addresses
- 2 = poor, misses key points
- 1 = useless, off-topic or empty

Output ONLY a JSON object, no preamble:
{{"score": <int 1-5>, "reason": "<one sentence>"}}

User question: {question}
Assistant answer: {answer}
"""


def judge_helpfulness(question: str, answer: str) -> tuple[float, str]:
    """调另一个 LLM 评估 helpfulness。
    返回 (normalized_score_0_to_1, reason)。
    失败 fallback 到 (0.5, parse_error_msg)。
    """
    judge_llm = ChatOpenAI(
        model="MiniMax-M3",  # 实战里用不同模型；这里同 model 演示
        api_key=os.getenv("MINIMAX_API_KEY"),
        base_url=os.getenv("MINIMAX_BASE_URL"),
        temperature=0,  # ← 关键：低 temperature 让 judge 稳定
    )

    try:
        resp = judge_llm.invoke(
            JUDGE_PROMPT.format(question=question, answer=answer)
        )
        text = resp.content

        # 主路径：JSON.parse
        parsed = json.loads(text)
        raw_score = float(parsed["score"])
        if not (1.0 <= raw_score <= 5.0):
            return 0.5, f"out-of-range: {raw_score}"

        # normalize 到 0-1（Langfuse NUMERIC 习惯）
        normalized = (raw_score - 1) / 4
        return normalized, parsed.get("reason", "")

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # Fallback：regex 抢救
        m = re.search(r'"score"\s*:\s*(\d+)', text if "text" in dir() else "")
        if m:
            try:
                raw = float(m.group(1))
                if 1.0 <= raw <= 5.0:
                    return (raw - 1) / 4, f"recovered_from_regex: {text[:60]}"
            except ValueError:
                pass
        return 0.5, f"parse_error: {type(e).__name__}; raw={text[:80]}"


# ---------- 4. 复用 lesson 0004 的 prompt 准备 ----------


def ensure_prompt_v1() -> int:
    """幂等创建 v1，复用 0004 的逻辑。"""
    lf = get_client()
    try:
        existing = lf.get_prompt(PROMPT_NAME, label="production")
        return existing.version
    except LangfuseError:
        pass

    lf.create_prompt(
        name=PROMPT_NAME,
        type="text",
        prompt=(
            "As a {{criticlevel}} movie critic, "
            "give a one-line take on {{movie}}."
        ),
        labels=["production"],
        config={"temperature": 0.7, "model": "MiniMax-M3"},
        commit_message="initial version",
    )
    return lf.get_prompt(PROMPT_NAME).version


# ---------- 5. 跑：拿 LLM 输出 + 三个 evaluator ----------


@observe(as_type="generation", name="ask-critic")
def ask_and_score(criticlevel: str, movie: str) -> str:
    """跑一次 LLM，并行应用 3 个 evaluator（2 Code + 1 LLM Judge）。"""
    lf = get_client()

    # 拿 prompt + compile
    p = lf.get_prompt(PROMPT_NAME)
    compiled = p.compile(criticlevel=criticlevel, movie=movie)

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
    answer = resp.content

    usage_md = resp.usage_metadata or {}
    lf.update_current_generation(
        usage_details={
            "input_tokens": usage_md.get("input_tokens", 0),
            "output_tokens": usage_md.get("output_tokens", 0),
            "total_tokens": usage_md.get("total_tokens", 0),
        },
    )

    # ----- Code Evaluator 1: output_present -----
    v, c = rule_output_present(answer)
    lf.score_current_trace(
        name="rule-output-present",
        value=v,
        data_type="NUMERIC",  # 0/1 用 NUMERIC 表达（BOOLEAN 也行，看 config）
        comment=c,
    )

    # ----- Code Evaluator 2: response_length -----
    v, c = rule_response_length(answer)
    lf.score_current_trace(
        name="rule-response-length",
        value=v,
        data_type="NUMERIC",
        comment=c,
    )

    # ----- LLM-as-a-Judge: helpfulness -----
    v, c = judge_helpfulness(question=f"评论《{movie}》", answer=answer)
    lf.score_current_trace(
        name="judge-helpfulness",
        value=v,
        data_type="NUMERIC",
        comment=c,
    )

    return answer


# ---------- 6. main ----------


def main() -> None:
    v = ensure_prompt_v1()
    print(f">> using movie-critic v{v}\n")

    answer = ask_and_score("harsh", "肖申克的救赎")
    print(f">> output: {answer[:120]}...")

    # 把 score 信息打出来（从 handler 拿）
    handler = get_client()
    print(f"\ntrace id = {handler.last_trace_id if hasattr(handler, 'last_trace_id') else '(see UI)'}")

    get_client().flush()
    print("\n✓ all scores written. check UI: http://localhost:3000")


if __name__ == "__main__":
    main()