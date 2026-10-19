"""Lesson 0006 · Dataset + Experiment 离线评估 demo.

演示 4 件事：
1. 建 Dataset（10 条电影评论样本）
2. 写 task function（复用 lesson 0004 的 movie-critic prompt）
3. 复用 lesson 0005-02 的 judge_helpfulness 作为 evaluator
4. 跑两次 experiment：v2 (production) vs v3 (staging)，对比 score

复用资源：
- lesson 0004 的 `movie-critic` prompt（已在 Langfuse 里）
- lesson 0005-02 的 `judge_helpfulness` LLM Judge

Usage:
    uv run python -m src.apps.fuse.experiment_demo
"""

import json
import os
import re
import sys

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langfuse import get_client
from langfuse.api.commons.errors.error import Error as LangfuseError
from langfuse.experiment import Evaluation

# 复用 lesson 0005-02 的 judge
from src.apps.fuse.scoring_demo import judge_helpfulness

load_dotenv()

DATASET_NAME = "movie-critic-eval-v1"
PROMPT_NAME = "movie-critic"

# 10 条样本：覆盖不同风格 + 不同电影
DATASET_ITEMS = [
    {"id": "q1",  "input": {"criticlevel": "harsh",  "movie": "Dune 2"}},
    {"id": "q2",  "input": {"criticlevel": "kind",   "movie": "Barbie"}},
    {"id": "q3",  "input": {"criticlevel": "neutral", "movie": "Oppenheimer"}},
    {"id": "q4",  "input": {"criticlevel": "harsh",  "movie": "The Marvels"}},
    {"id": "q5",  "input": {"criticlevel": "kind",   "movie": "Elemental"}},
    {"id": "q6",  "input": {"criticlevel": "neutral", "movie": "Killers of the Flower Moon"}},
    {"id": "q7",  "input": {"criticlevel": "harsh",  "movie": "Madame Web"}},
    {"id": "q8",  "input": {"criticlevel": "kind",   "movie": "Wonka"}},
    {"id": "q9",  "input": {"criticlevel": "neutral", "movie": "Past Lives"}},
    {"id": "q10", "input": {"criticlevel": "harsh",  "movie": "Morbius"}},
]

# 公共 LLM（task 和 judge 共用；实战 judge 用 gpt-4o-mini 等不同模型）
llm = ChatOpenAI(
    model="MiniMax-M3",
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url=os.getenv("MINIMAX_BASE_URL"),
    temperature=0.7,
)


# ---------- 1. 建 Dataset（幂等） ----------


def ensure_dataset() -> None:
    """幂等创建 dataset + 10 条 item。"""
    lf = get_client()
    try:
        lf.get_dataset(DATASET_NAME)
        print(f"✓ dataset '{DATASET_NAME}' already exists")
    except Exception:
        # NotFoundError / 任何 API 错误都视作"不存在" → 创建
        # （Langfuse SDK 4.x 里 Error 和 NotFoundError 是兄弟节点，单独 except 接不到）
        lf.create_dataset(
            name=DATASET_NAME,
            description="Evaluate movie-critic prompt across 10 sample films × 3 critic levels",
        )
        print(f"✓ created dataset '{DATASET_NAME}'")

    # 批量 upsert items
    for it in DATASET_ITEMS:
        lf.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=it["id"],
            input=it["input"],
            expected_output=None,  # LLM 输出不确定，留空用 LLM Judge 评
        )
    print(f"✓ {len(DATASET_ITEMS)} items upserted")


# ---------- 2. Task function ----------


def make_task(label: str):
    """返回一个 task closure：拿指定 label 的 prompt 跑 input → output。

    v4 SDK 调 task 时用 keyword: task(item=ExperimentItem)，
    ExperimentItem 是 TypedDict 含 input / expected_output / metadata。
    """
    lf = get_client()
    prompt_obj = lf.get_prompt(PROMPT_NAME, label=label)

    def task(item, **kwargs) -> str:
        # item 是 TypedDict，input 字段是用户存的 dict
        item_input = item["input"] if isinstance(item, dict) else item.input
        compiled = prompt_obj.compile(**item_input)
        resp = llm.invoke(
            [
                {"role": "system", "content": compiled},
                {"role": "user", "content": f"评论一下《{item_input['movie']}》"},
            ]
        )
        return resp.content

    return task, prompt_obj.version


# ---------- 3. Evaluators（实验场景签名）----------


def evaluator_exact_match(*, input, output, expected_output, **kwargs):
    """黄金集对比：本 demo 的 expected_output 留空 → 永远跳过。"""
    if expected_output is None:
        return None
    return Evaluation(
        name="exact-match",
        value=1.0 if output == expected_output else 0.0,
        data_type="NUMERIC",
    )


def evaluator_llm_judge(*, input, output, **kwargs):
    """复用 lesson 0005-02 的 judge_helpfulness。

    v4 SDK 传 kwargs：
        input = dataset_item.input 字段（直接 dict）
        output = task 函数返回值
        expected_output = dataset_item.expected_output（可 None）
        metadata = 合并 metadata
    """
    question = f"评论（{input.get('criticlevel', '?')}）"
    value, comment = judge_helpfulness(question, output)
    return Evaluation(
        name="judge-helpfulness",
        value=value,
        data_type="NUMERIC",
        comment=comment,
    )


def evaluator_keyword_match(*, input, output, **kwargs):
    """检查 output 是否包含电影名（业务硬指标：评对了片）。"""
    movie = input.get("movie", "")
    return Evaluation(
        name="keyword-match",
        value=1.0 if movie.lower() in output.lower() else 0.0,
        data_type="NUMERIC",
    )


EVALUATORS = [
    evaluator_exact_match,
    evaluator_llm_judge,
    evaluator_keyword_match,
]


# ---------- 4. 跑两次 experiment ----------


def run_experiment(name: str, label: str) -> dict:
    """跑一次 experiment，返回 summary。"""
    lf = get_client()
    task, version = make_task(label)
    print(f"\n>>> running experiment: {name}  (prompt v{version}, label={label})")

    dataset = lf.get_dataset(DATASET_NAME)
    dataset.run_experiment(
        name=name,
        description=f"Lesson 0006 demo · prompt label={label} (v{version})",
        task=task,
        evaluators=EVALUATORS,
    )
    print(f"✓ experiment '{name}' done — check UI: Datasets → {DATASET_NAME}")
    return {"name": name, "version": version, "label": label}


def main() -> None:
    ensure_dataset()

    # 实验 1：用 production 版本（v2，演示里这是基线）
    run_experiment(name="v2-production-2026-09-16", label="production")

    # 实验 2：用 latest 版本（如果用户建了 v3）
    # 默认 fallback 到 production（这样 demo 不会因为没 v3 而失败）
    label_for_v3 = "latest" if os.getenv("HAS_V3") == "1" else "production"
    run_experiment(name=f"v3-{label_for_v3}-2026-09-16", label=label_for_v3)

    print("\n" + "=" * 70)
    print("对比两个 run（去 Langfuse UI 的 Dataset 页面看详细对比）：")
    print("  → http://localhost:3000 → Datasets → " + DATASET_NAME)
    print("=" * 70)

    get_client().flush()
    print("\n✓ all done")


if __name__ == "__main__":
    main()