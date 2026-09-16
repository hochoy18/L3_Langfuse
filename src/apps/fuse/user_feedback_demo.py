"""Lesson 0005-03 · User Feedback 上报 demo.

演示 3 件事：
1. 调用 Langfuse Python SDK 给已有 trace 打 user-feedback score
2. 演示通过 REST API 上报（Browser SDK / web 后端等价的 Python 版）
3. 用 propagation 检查 user-feedback score 是否真的落库

前置：先跑 scoring_demo.py 留一条 trace；然后跑本脚本给它打 👍 score。

Usage:
    # 1. 跑 scoring_demo.py → 打印 trace_id（如果没记，从 UI URL 拿）
    # 2. 把 trace_id 粘到下面 TRACE_ID_TO_SCORE 常量
    # 3. uv run python src/apps/fuse/user_feedback_demo.py
"""

import os

import urllib3
from dotenv import load_dotenv
from langfuse import get_client

load_dotenv()

# ← 替换成你要打分的那条 trace 的 ID（从 UI URL 或 scoring_demo 输出拿）
TRACE_ID_TO_SCORE = os.getenv("DEMO_TRACE_ID", "REPLACE_ME")


def path_a_python_sdk() -> None:
    """路径 A：Python SDK 直接调（推荐，后端转发用）。"""
    if TRACE_ID_TO_SCORE == "REPLACE_ME":
        print("⚠️ 请先在 .env 设 DEMO_TRACE_ID=<trace_id>")
        return

    lf = get_client()

    # 1. 👍 正向反馈
    lf.create_score(
        name="user-feedback",
        value=1,                    # BOOLEAN: 1 = 👍, 0 = 👎
        data_type="BOOLEAN",
        trace_id=TRACE_ID_TO_SCORE,
        comment="user clicked thumbs up (via Python SDK)",
    )

    # 2. 5星评分（CATEGORICAL）—— 同一 trace 多条 score 不冲突
    lf.create_score(
        name="user-rating",
        value="5_stars",
        data_type="CATEGORICAL",
        trace_id=TRACE_ID_TO_SCORE,
        comment="user rated 5 of 5",
    )

    print(f"✓ Python SDK: wrote user-feedback=1 + user-rating=5_stars to {TRACE_ID_TO_SCORE}")


def path_b_rest_api() -> None:
    """路径 B：REST API 等价于 web Browser SDK 的服务端转发。"""
    if TRACE_ID_TO_SCORE == "REPLACE_ME":
        print("⚠️ 请先在 .env 设 DEMO_TRACE_ID=<trace_id>")
        return

    host = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000").rstrip("/")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    if not (public_key and secret_key):
        print("⚠️ 请先在 .env 设 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY")
        return

    # urllib3 Basic Auth + JSON POST（等价于 requests.post(auth=(pk, sk), json=...)）
    http = urllib3.PoolManager()
    resp = http.request(
        "POST",
        f"{host}/api/public/scores",
        headers={"Content-Type": "application/json"},
        body=__import__("json").dumps({
            "traceId": TRACE_ID_TO_SCORE,
            "name": "user-feedback-rest",
            "value": 0,                # 👎 负面反馈（演示用，跟上面 SDK 的 👍 不冲突）
            "dataType": "BOOLEAN",
            "comment": "via REST API (urllib3)",
        }).encode("utf-8"),
    )

    # Basic Auth header
    import base64
    auth_str = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    resp = http.request(
        "POST",
        f"{host}/api/public/scores",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_str}",
        },
        body=__import__("json").dumps({
            "traceId": TRACE_ID_TO_SCORE,
            "name": "user-feedback-rest",
            "value": 0,
            "dataType": "BOOLEAN",
            "comment": "via REST API (urllib3, Basic Auth)",
        }).encode("utf-8"),
    )

    if resp.status in (200, 201):
        print(f"✓ REST API: status={resp.status}, wrote user-feedback-rest=0")
    else:
        print(f"✗ REST API failed: status={resp.status}, body={resp.data.decode()[:200]}")


def path_c_verify() -> None:
    """路径 C：用 SDK 的低层 API 验证 score 是否真的入库。"""
    if TRACE_ID_TO_SCORE == "REPLACE_ME":
        return

    lf = get_client()
    try:
        # 通过 get_client().fetch_trace 或直接 fetch observations
        # 这里用 v2 observations API（v4 events_only 模式下唯一可用的）
        trace = lf.fetch_trace(TRACE_ID_TO_SCORE)
        print(f"\n✓ trace found: {trace.id}")
        print(f"  scores count: {len(trace.scores)}")
        for s in trace.scores:
            print(f"  - {s.name} = {s.value} ({s.data_type})")
    except Exception as e:
        print(f"⚠️ verify failed: {e}")


def main() -> None:
    print("=" * 60)
    print("Path A: Python SDK")
    print("=" * 60)
    path_a_python_sdk()

    print("\n" + "=" * 60)
    print("Path B: REST API (urllib3 + Basic Auth)")
    print("=" * 60)
    path_b_rest_api()

    print("\n" + "=" * 60)
    print("Path C: Verify (读回确认)")
    print("=" * 60)
    path_c_verify()

    get_client().flush()
    print("\n✓ all done. check UI: http://localhost:3000")


if __name__ == "__main__":
    main()