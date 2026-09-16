"""PostgreSQL 链接查询示例 — Langfuse 数据模型.

演示 6 类跨表 JOIN 查询模式：
1. trace → observations (1:n)
2. trace → scores (1:n)
3. observation → scores (1:n)
4. trace → session → 用户级聚合
5. trace → prompt_version（用 prompt_name + version 关联）
6. trace → dataset_run_item（实验来源追溯）

支持两种运行模式：
- DEFAULT（sample 模式）：脚本自己创建表 + 插测试数据，运行无需真实 Langfuse
- PRODUCTION 模式：连接真实 Langfuse Postgres（用 DATABASE_URL 环境变量）

Usage:
    # 跑 sample 模式（首次会建表 + 插数据，第二次直接跑）
    uv run python src/examples/pg/link_queries.py

    # 跑特定 query
    uv run python src/examples/pg/link_queries.py --query 2

    # 跑 production 模式
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
      uv run python src/examples/pg/link_queries.py --mode prod
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager

import psycopg
from psycopg import rows


# ---------------------------------------------------------------------------
# 1. 连接管理
# ---------------------------------------------------------------------------

os.environ["DATABASE_URL"] = "postgresql://admin:admin#123@pg.bananahochoy.online/xui"

def get_connection_string() -> str:
    """sample 模式用本地 Postgres，prod 模式用 DATABASE_URL 环境变量。"""
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL", "")
    # sample 模式默认连接（cicd/docker-compose.yml 默认账号）
    return "postgresql://postgres:postgres@localhost:5432/postgres"


@contextmanager
def get_conn(sample: bool = False):
    """psycopg3 连接 context manager，自动 commit / 关闭。"""
    dsn = get_connection_string()  # 两种模式用同一连接串；schema 隔离
    conn = psycopg.connect(dsn, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Sample 数据 bootstrap
# ---------------------------------------------------------------------------


SAMPLE_SCHEMA = "langfuse_pg_demo"


SAMPLE_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SAMPLE_SCHEMA};

DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.dataset_run_items CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.dataset_runs CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.dataset_items CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.prompt_versions CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.prompts CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.scores CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.observations CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.sessions CASCADE;
DROP TABLE IF EXISTS {SAMPLE_SCHEMA}.traces CASCADE;

-- traces：顶层节点
CREATE TABLE {SAMPLE_SCHEMA}.traces (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    user_id         TEXT,
    session_id      TEXT,
    environment     TEXT,
    release         TEXT,
    tags            TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- observations：1 个 trace 下挂多个 observation（树形）
CREATE TABLE {SAMPLE_SCHEMA}.observations (
    id                      TEXT PRIMARY KEY,
    trace_id                TEXT NOT NULL REFERENCES {SAMPLE_SCHEMA}.traces(id),
    parent_observation_id   TEXT,
    type                    TEXT NOT NULL,  -- SPAN / GENERATION / TOOL / AGENT
    name                    TEXT NOT NULL,
    model                   TEXT,           -- GENERATION 才有
    prompt_name             TEXT,           -- GENERATION 才有，关联 prompt_versions
    prompt_version          INT,
    input_tokens            INT DEFAULT 0,
    output_tokens           INT DEFAULT 0,
    start_time              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON {SAMPLE_SCHEMA}.observations (trace_id);
CREATE INDEX ON {SAMPLE_SCHEMA}.observations (parent_observation_id);

-- scores：可挂 trace 或 observation
CREATE TABLE {SAMPLE_SCHEMA}.scores (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT REFERENCES {SAMPLE_SCHEMA}.traces(id),
    observation_id  TEXT REFERENCES {SAMPLE_SCHEMA}.observations(id),
    name            TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    data_type       TEXT NOT NULL,           -- NUMERIC / CATEGORICAL / BOOLEAN
    string_value    TEXT,                   -- CATEGORICAL 时填这个
    source          TEXT NOT NULL,           -- SDK / API / ANNOTATION_QUEUE
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (trace_id IS NOT NULL OR observation_id IS NOT NULL)
);
CREATE INDEX ON {SAMPLE_SCHEMA}.scores (trace_id);
CREATE INDEX ON {SAMPLE_SCHEMA}.scores (observation_id);

-- sessions：多 trace 归组
CREATE TABLE {SAMPLE_SCHEMA}.sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- prompts + versions：版本化 prompt
CREATE TABLE {SAMPLE_SCHEMA}.prompts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);
CREATE TABLE {SAMPLE_SCHEMA}.prompt_versions (
    id          TEXT PRIMARY KEY,
    prompt_id   TEXT NOT NULL REFERENCES {SAMPLE_SCHEMA}.prompts(id),
    version     INT NOT NULL,
    label       TEXT,  -- production / staging / latest
    UNIQUE(prompt_id, version)
);

-- datasets：离线实验
CREATE TABLE {SAMPLE_SCHEMA}.dataset_items (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    input           JSONB NOT NULL,
    expected_output JSONB
);
CREATE TABLE {SAMPLE_SCHEMA}.dataset_runs (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE {SAMPLE_SCHEMA}.dataset_run_items (
    id                  TEXT PRIMARY KEY,
    dataset_run_id      TEXT NOT NULL REFERENCES {SAMPLE_SCHEMA}.dataset_runs(id),
    dataset_item_id     TEXT NOT NULL REFERENCES {SAMPLE_SCHEMA}.dataset_items(id),
    trace_id            TEXT REFERENCES {SAMPLE_SCHEMA}.traces(id)
);
"""


SAMPLE_DATA = f"""
-- 3 个 session，5 个 user
INSERT INTO {SAMPLE_SCHEMA}.sessions (id) VALUES
    ('sess-alice'), ('sess-bob'), ('sess-charlie');

-- 5 个 trace（不同 user、不同 session）
INSERT INTO {SAMPLE_SCHEMA}.traces (id, name, user_id, session_id, environment, release, tags) VALUES
    ('t1', 'checkout_recommend', 'u-alice', 'sess-alice',   'prod',    'v2.1.0', ARRAY['prod','recommend']),
    ('t2', 'checkout_recommend', 'u-alice', 'sess-alice',   'prod',    'v2.1.0', ARRAY['prod','recommend']),
    ('t3', 'movie_critic',       'u-bob',   'sess-bob',     'prod',    'v2.1.0', ARRAY['prod','chat']),
    ('t4', 'movie_critic',       'u-bob',   'sess-bob',     'staging', 'v2.2.0-beta', ARRAY['staging','chat']),
    ('t5', 'checkout_recommend', 'u-charlie','sess-charlie','prod',    'v2.1.0', ARRAY['prod','recommend']);

-- t1 有 4 个 observation（agent → llm → tool → llm 树）
INSERT INTO {SAMPLE_SCHEMA}.observations (id, trace_id, parent_observation_id, type, name, model, prompt_name, prompt_version, input_tokens, output_tokens) VALUES
    ('o1', 't1', NULL,           'AGENT',     'planner',     NULL, NULL,  NULL, 0, 0),
    ('o2', 't1', 'o1',           'GENERATION','ask_llm',      'MiniMax-M3', 'checkout-recommend', 2, 120, 80),
    ('o3', 't1', 'o1',           'TOOL',      'search_catalog', NULL, NULL, NULL, 0, 0),
    ('o4', 't1', 'o1',           'GENERATION','format_reply','MiniMax-M3', 'checkout-recommend', 2, 60, 40);

-- t2 简单 1 个 generation
INSERT INTO {SAMPLE_SCHEMA}.observations (id, trace_id, type, name, model, prompt_name, prompt_version, input_tokens, output_tokens) VALUES
    ('o5', 't2', 'GENERATION', 'ask_llm', 'MiniMax-M3', 'checkout-recommend', 1, 100, 70);

-- t3 / t4 movie_critic，各 1 个 generation
INSERT INTO {SAMPLE_SCHEMA}.observations (id, trace_id, type, name, model, prompt_name, prompt_version, input_tokens, output_tokens) VALUES
    ('o6', 't3', 'GENERATION', 'ask_critic', 'MiniMax-M3', 'movie-critic', 1, 200, 150),
    ('o7', 't4', 'GENERATION', 'ask_critic', 'MiniMax-M3', 'movie-critic', 2, 210, 160);

-- t5 1 个 generation
INSERT INTO {SAMPLE_SCHEMA}.observations (id, trace_id, type, name, model, prompt_name, prompt_version, input_tokens, output_tokens) VALUES
    ('o8', 't5', 'GENERATION', 'ask_llm', 'MiniMax-M3', 'checkout-recommend', 2, 130, 90);

-- 评分：覆盖 trace / observation / 不同 data_type / 不同 source
INSERT INTO {SAMPLE_SCHEMA}.scores (id, trace_id, observation_id, name, value, data_type, string_value, source) VALUES
    -- trace-level
    ('s1',  't1', NULL, 'helpfulness',    0.85, 'NUMERIC',     NULL,         'SDK'),
    ('s2',  't1', NULL, 'user-feedback',  1.0,  'NUMERIC',     NULL,         'API'),
    ('s3',  't2', NULL, 'helpfulness',    0.72, 'NUMERIC',     NULL,         'SDK'),
    ('s4',  't3', NULL, 'tone',           0.0,  'NUMERIC',     NULL,         'SDK'),
    ('s5',  't3', NULL, 'tone',           0.0,  'NUMERIC',     NULL,         'SDK'),  -- 重复 name（user-feedback 当 ground truth 时的反例）
    ('s6',  't4', NULL, 'helpfulness',    0.91, 'NUMERIC',     NULL,         'SDK'),
    -- observation-level
    ('s7',  't1', 'o2',  'llm-specific',  0.9,  'NUMERIC',     NULL,         'SDK'),
    ('s8',  't1', 'o4',  'llm-specific',  0.7,  'NUMERIC',     NULL,         'SDK'),
    -- CATEGORICAL
    ('s9',  't3', NULL, 'quality',        0.0,  'CATEGORICAL', 'good',      'ANNOTATION_QUEUE'),
    ('s10', 't4', NULL, 'quality',        0.0,  'CATEGORICAL', 'bad',       'ANNOTATION_QUEUE'),
    -- BOOLEAN
    ('s11', 't1', NULL, 'json-valid',     1.0,  'NUMERIC',     NULL,         'SDK'),
    ('s12', 't3', NULL, 'json-valid',     0.0,  'NUMERIC',     NULL,         'SDK');

-- prompts + versions
INSERT INTO {SAMPLE_SCHEMA}.prompts (id, name) VALUES
    ('p1', 'checkout-recommend'),
    ('p2', 'movie-critic');

INSERT INTO {SAMPLE_SCHEMA}.prompt_versions (id, prompt_id, version, label) VALUES
    ('pv1', 'p1', 1, NULL),
    ('pv2', 'p1', 2, 'production'),
    ('pv3', 'p2', 1, NULL),
    ('pv4', 'p2', 2, 'production');

-- dataset + 实验
INSERT INTO {SAMPLE_SCHEMA}.dataset_items (id, dataset_id, input, expected_output) VALUES
    ('di1', 'eval-v1', '{{"q":"recommend for me"}}', '{{"items":["a","b"]}}'),
    ('di2', 'eval-v1', '{{"q":"more please"}}',       NULL);

INSERT INTO {SAMPLE_SCHEMA}.dataset_runs (id, dataset_id, name) VALUES
    ('dr1', 'eval-v1', 'v2.1.0-vs-v2.2.0');

-- t3 / t4 是 eval 跑出来的 trace
INSERT INTO {SAMPLE_SCHEMA}.dataset_run_items (id, dataset_run_id, dataset_item_id, trace_id) VALUES
    ('dri1', 'dr1', 'di1', 't3'),
    ('dri2', 'dr1', 'di2', 't4');
"""


def bootstrap_sample_data(conn) -> None:
    """建表 + 插数据，仅在 sample 模式调用。"""
    with conn.cursor() as cur:
        cur.execute(SAMPLE_DDL)
        cur.execute(SAMPLE_DATA)
    print(f"✓ bootstrapped sample schema '{SAMPLE_SCHEMA}' with 5 traces + 8 observations + 12 scores")


# ---------------------------------------------------------------------------
# 3. 6 个 Link Query 示例
# ---------------------------------------------------------------------------


def q1_trace_full_tree(conn) -> None:
    """Q1: trace → observations (1:n 树形结构 + parent 嵌套).

    用窗口函数 / 自连接把 parent_observation_id 解析成树形。
    """
    sql = f"""
    SELECT
        t.id           AS trace_id,
        t.name         AS trace_name,
        o.id           AS obs_id,
        o.type         AS obs_type,
        o.name         AS obs_name,
        o.parent_observation_id,
        -- 算每个 trace 的 observation 总数 + 总 token
        COUNT(*) OVER (PARTITION BY t.id) AS obs_count,
        SUM(o.input_tokens) OVER (PARTITION BY t.id) AS trace_total_input_tokens,
        SUM(o.output_tokens) OVER (PARTITION BY t.id) AS trace_total_output_tokens
    FROM {SAMPLE_SCHEMA}.traces t
    JOIN {SAMPLE_SCHEMA}.observations o ON o.trace_id = t.id
    WHERE t.id IN ('t1', 't3')
    ORDER BY t.id, o.start_time;
    """
    print("\n" + "=" * 70)
    print("Q1: trace → observations 树形 + 窗口函数聚合 token")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            print(f"  trace={row[0]:<5} obs={row[2]:<5} ({row[3]:<10}/{row[4]:<15}) tokens_in={row[7]} obs_count={row[6]}")


def q2_trace_scores_aggregation(conn) -> None:
    """Q2: trace → scores (1:n)，按 data_type 分桶 + 按 source 分组."""
    sql = f"""
    SELECT
        t.id                                                              AS trace_id,
        COUNT(*)                                                          AS score_count,
        -- NUMERIC 聚合
        AVG(CASE WHEN s.data_type='NUMERIC' THEN s.value END)             AS avg_numeric,
        -- BOOLEAN 当 NUMERIC 看待（0/1），算 pass rate
        AVG(CASE WHEN s.data_type='NUMERIC' AND s.name='json-valid' THEN s.value END) AS pass_rate,
        -- CATEGORICAL 列表（每个 trace 的所有 string_value）
        STRING_AGG(s.name || ':' || COALESCE(s.string_value, '-'), ', '
                    ORDER BY s.name)                                       AS categorical_summary,
        -- source 分组计数
        STRING_AGG(s.source || '×' || s.name, ', ' ORDER BY s.source)     AS sources
    FROM {SAMPLE_SCHEMA}.traces t
    LEFT JOIN {SAMPLE_SCHEMA}.scores s ON s.trace_id = t.id
    GROUP BY t.id
    HAVING COUNT(*) > 0                -- 只看有 score 的 trace
    ORDER BY score_count DESC;
    """
    print("\n" + "=" * 70)
    print("Q2: trace → scores 聚合（按 trace 一行：NUMERIC avg + CATEGORICAL 列表 + source 分组）")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            print(f"  trace={row[0]:<5} scores={row[1]} avg_numeric={row[2]} pass_rate={row[3]}")
            print(f"           categorical: {row[4]}")
            print(f"           sources: {row[5]}")


def q3_observation_scores(conn) -> None:
    """Q3: observation → scores (1:n)，看具体 step 的评分."""
    sql = f"""
    SELECT
        o.trace_id,
        o.id        AS observation_id,
        o.name      AS observation_name,
        o.type      AS observation_type,
        s.name      AS score_name,
        s.value     AS score_value,
        s.data_type,
        s.source
    FROM {SAMPLE_SCHEMA}.observations o
    LEFT JOIN {SAMPLE_SCHEMA}.scores s ON s.observation_id = o.id
    WHERE o.type IN ('GENERATION', 'TOOL')
    ORDER BY o.trace_id, o.id, s.name;
    """
    print("\n" + "=" * 70)
    print("Q3: observation → scores（看具体 step 的局部评分）")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            score_str = f"{row[4]}={row[5]}" if row[4] else "(no score)"
            print(f"  trace={row[0]} obs={row[1]} ({row[3]:<10}) {score_str}")


def q4_prompt_version_link(conn) -> None:
    """Q4: trace → observation → prompt → prompt_version（4 表链接）.

    'Langfuse UI 上 Generation 的 Prompt 徽章' 在 Postgres 里的实现：
    observation.prompt_name + prompt_version → prompts.name → prompt_versions.version + label
    """
    sql = f"""
    SELECT
        o.trace_id,
        o.id           AS generation_id,
        o.name         AS generation_name,
        o.model,
        p.name         AS prompt_name,
        pv.version     AS prompt_version,
        pv.label       AS prompt_label,
        -- 算每个 prompt 版本被使用的次数
        COUNT(*) OVER (PARTITION BY p.name, pv.version) AS usage_count,
        -- 算这个 prompt 版本在所有 score 里的平均 helpfulness
        (SELECT AVG(s.value)
         FROM {SAMPLE_SCHEMA}.scores s
         WHERE s.trace_id = o.trace_id AND s.name = 'helpfulness') AS trace_helpfulness
    FROM {SAMPLE_SCHEMA}.observations o
    JOIN {SAMPLE_SCHEMA}.prompts p         ON p.name = o.prompt_name
    JOIN {SAMPLE_SCHEMA}.prompt_versions pv ON pv.prompt_id = p.id AND pv.version = o.prompt_version
    WHERE o.type = 'GENERATION' AND o.prompt_name IS NOT NULL
    ORDER BY o.trace_id, o.id;
    """
    print("\n" + "=" * 70)
    print("Q4: trace → observation → prompt → prompt_version（Prompt 徽章的 Postgres 实现）")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            print(f"  trace={row[0]} prompt={row[4]} v{row[5]} ({row[6] or '-'}) used={row[7]} helpfulness={row[8]}")


def q5_session_user_aggregation(conn) -> None:
    """Q5: session → traces → observations → scores（4 层聚合 + 用户维度）.

    按 session 汇总：trace 数、observation 数、token 总和、平均 helpfulness
    """
    sql = f"""
    SELECT
        s.id                                                              AS session_id,
        t.user_id,
        COUNT(DISTINCT t.id)                                               AS trace_count,
        COUNT(DISTINCT o.id)                                              AS observation_count,
        SUM(COALESCE(o.input_tokens, 0) + COALESCE(o.output_tokens, 0))   AS total_tokens,
        AVG(CASE WHEN sc.name='helpfulness' THEN sc.value END)            AS avg_helpfulness,
        AVG(CASE WHEN sc.name='user-feedback' THEN sc.value END)           AS avg_user_feedback
    FROM {SAMPLE_SCHEMA}.sessions s
    JOIN {SAMPLE_SCHEMA}.traces t       ON t.session_id = s.id
    LEFT JOIN {SAMPLE_SCHEMA}.observations o ON o.trace_id = t.id
    LEFT JOIN {SAMPLE_SCHEMA}.scores sc      ON sc.trace_id = t.id
    GROUP BY s.id, t.user_id
    ORDER BY total_tokens DESC;
    """
    print("\n" + "=" * 70)
    print("Q5: session → trace → observation → score（4 层聚合 + user 维度）")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            print(f"  session={row[0]:<15} user={row[1]:<10} traces={row[2]} obs={row[3]} tokens={row[4]} "
                  f"helpfulness={row[5]} user_feedback={row[6]}")


def q6_experiment_lineage(conn) -> None:
    """Q6: trace → dataset_run_item → dataset_item（实验来源追溯）.

    给定一条 trace，逆向追溯它是从哪个 dataset run 哪条 item 跑出来的；
    同时正向：找出 run 里所有 trace 的平均 score 对比
    """
    sql = f"""
    WITH run_stats AS (
        SELECT
            dr.id                AS run_id,
            dr.name              AS run_name,
            di.id                AS item_id,
            t.id                 AS trace_id,
            t.user_id,
            AVG(CASE WHEN s.name='helpfulness' THEN s.value END) AS helpfulness,
            AVG(CASE WHEN s.name='quality' AND s.data_type='CATEGORICAL' THEN NULL ELSE s.value END) AS numeric_avg
        FROM {SAMPLE_SCHEMA}.dataset_runs dr
        JOIN {SAMPLE_SCHEMA}.dataset_run_items dri ON dri.dataset_run_id = dr.id
        JOIN {SAMPLE_SCHEMA}.dataset_items di      ON di.id = dri.dataset_item_id
        JOIN {SAMPLE_SCHEMA}.traces t              ON t.id = dri.trace_id
        LEFT JOIN {SAMPLE_SCHEMA}.scores s         ON s.trace_id = t.id
        GROUP BY dr.id, dr.name, di.id, t.id, t.user_id
    )
    SELECT
        run_name,
        item_id,
        trace_id,
        user_id,
        helpfulness,
        numeric_avg,
        -- 同 run 内所有 trace 的平均 helpfulness（看这条 trace 是否高于均值）
        AVG(helpfulness) OVER (PARTITION BY run_name) AS run_avg_helpfulness,
        helpfulness - AVG(helpfulness) OVER (PARTITION BY run_name) AS delta
    FROM run_stats
    ORDER BY run_name, item_id;
    """
    print("\n" + "=" * 70)
    print("Q6: dataset_run → dataset_item → trace → score（实验来源追溯 + run 内对比）")
    print("=" * 70)
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            delta_str = f"{row[7]:+.2f}" if row[7] is not None else "—"
            helpfulness_str = f"{row[4]:.2f}" if row[4] is not None else "—"
            print(f"  run={row[0]:<25} item={row[1]} trace={row[2]} helpfulness={helpfulness_str} delta={delta_str}")


QUERIES = {
    "1": ("trace → observations 树 + 窗口函数 token 聚合", q1_trace_full_tree),
    "2": ("trace → scores 聚合（NUMERIC avg + CATEGORICAL 列表）", q2_trace_scores_aggregation),
    "3": ("observation → scores（具体 step 评分）", q3_observation_scores),
    "4": ("trace → observation → prompt → prompt_version", q4_prompt_version_link),
    "5": ("session → trace → obs → score（4 层聚合）", q5_session_user_aggregation),
    "6": ("dataset_run → item → trace → score（实验追溯）", q6_experiment_lineage),
}


# ---------------------------------------------------------------------------
# 4. 入口
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=["sample", "prod"], default="sample",
                        help="sample = 自己建表插数据；prod = 连真实 Langfuse")
    parser.add_argument("--query", choices=list(QUERIES.keys()) + ["all"], default="all",
                        help="跑哪个 query；默认全部")
    parser.add_argument("--bootstrap", action="store_true",
                        help="强制重新 bootstrap sample 数据")
    args = parser.parse_args()

    sample_mode = args.mode == "sample"

    print(f"mode = {args.mode}")
    print(f"query = {args.query}")
    print(f"dsn  = {'(sample schema: ' + SAMPLE_SCHEMA + ')' if sample_mode else os.getenv('DATABASE_URL', '(none)')}")

    with get_conn(sample=sample_mode) as conn:
        if sample_mode:
            # 检查是否需要 bootstrap
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
 "WHERE table_schema = %s AND table_name = 'traces')",
                    (SAMPLE_SCHEMA,),
                )
                exists = cur.fetchone()[0]
            if not exists or args.bootstrap:
                bootstrap_sample_data(conn)
            else:
                print(f"✓ schema '{SAMPLE_SCHEMA}' already exists, skip bootstrap (use --bootstrap to recreate)")

        # 跑 queries
        if args.query == "all":
            for qid, (desc, fn) in QUERIES.items():
                fn(conn)
        else:
            desc, fn = QUERIES[args.query]
            print(f"\nrunning: {desc}")
            fn(conn)


if __name__ == "__main__":
    main()