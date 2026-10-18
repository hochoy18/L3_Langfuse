# ADR-0005 · PostgresSaver 复用 langfuse-deploy 的 Postgres

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `graph.py` + 部署依赖 |

## Context

LangGraph 的 `interrupt()` 模式**必须**配 checkpointer 才能 resume，否则图状态在 `__interrupt__` 返回时丢失，Streamlit 用户关掉 terminal 后无法续跑。

三种 checkpointer 选择：
1. **MemorySaver**：内存，进程退出即丢。CLI demo 勉强可用，Streamlit 不可用
2. **SqliteSaver**：本地文件，零外部依赖。CLI/单进程可用，但无法跨进程 resume
3. **PostgresSaver**：外部 Postgres，跨进程、跨重启、跨机器 resume

## Decision

**采用 PostgresSaver，连接 langfuse-deploy 已部署的 Postgres 实例**。

```python
# graph.py
from langgraph.checkpoint.postgres import PostgresSaver

DB_URI = os.getenv(
    "LANGGRAPH_CHECKPOINT_DB",
    "postgresql://postgres:postgres@localhost:5432/postgres",  # langfuse-deploy 的 postgres
)

checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()  # 自动建 checkpoints / checkpoint_blobs / checkpoint_writes 表

graph = builder.compile(checkpointer=checkpointer)
```

复用 langfuse-deploy 的 Postgres（端口 5432 已暴露 127.0.0.1，详见 `../../langfuse-deploy/docker-compose.yml`）。LangGraph 自动创建 `checkpoints` 等表，与 Langfuse 自己的表互不干扰。

## Alternatives Considered

### A. MemorySaver

```python
from langgraph.checkpoint.memory import MemorySaver
checkpointer = MemorySaver()
```

**否定原因**：
- Streamlit 每次 rerun 都是新进程（或新 connection），状态必丢
- 即便单进程，Streamlit `st.rerun()` 触发的 invoke 可能在不同 worker，状态对不上

### B. SqliteSaver

```python
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string("~/.langgraph_checkpoints.db")
```

**否定原因**：
- 文件锁问题：Streamlit + interrupt + 多 worker 容易 race condition
- 跨机器 resume 不可能（未来若 agent 部署到 server）
- 与已有 Postgres 重复造轮子

### C. 新建独立 Postgres

**否定原因**：
- 与 langfuse-deploy 的 Postgres 重复（多一个容器 = 多一份维护成本）
- 学习项目不需要这种隔离

## Consequences

**正向**：
- 状态持久化到已运维成熟的 Postgres（健康检查 / 备份 / 监控都现成）
- 跨进程 / 跨重启 resume：`thread_id` + checkpoint 存储分离，Streamlit UI 关闭后下次启动可续
- 同一 Postgres 实例承载 Langfuse 元数据 + LangGraph checkpoints，资源复用

**负向**：
- **强耦合**：deep_research agent 强依赖 langfuse-deploy Postgres 服务可用。若 langfuse-deploy 挂了，agent 也跑不起来
- `checkpointer.setup()` 首次运行要建表，需要 Postgres 用户有 CREATE 权限（默认 `postgres` 用户满足）
- `langgraph-checkpoint-postgres` 包需要加到 `pyproject.toml`（`langgraph >= 1.2` 已带，但显式声明）

**运维要求**：
- deep_research agent 启动前**必须**先 `cd langfuse-deploy && docker compose up -d`
- Postgres 健康检查：`docker exec langfuse-deploy-postgres-1 pg_isready -U postgres`

**未来触发条件**：如果 deep_research agent 要独立部署（脱离 langfuse-deploy），需建独立 Postgres 或迁 Sqlite——那时再评估。