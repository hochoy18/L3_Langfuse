# Lab · Langfuse + LangChain + LangGraph 实验场

这是一个用 [uv](https://docs.astral.sh/uv/) 管理的 Python 3.11 项目，
用来跟着 [../lessons/](../lessons/) 里的课程做实验。

## 当前包版本

| 包 | 版本 |
|---|---|
| langchain | 1.4.x |
| langchain-openai | 1.6.x |
| langgraph | 1.2.x |
| langfuse | 3.x |
| openai | 1.x |

## 快速开始

```bash
# 1. 装依赖（首次会创建 .venv/）
uv sync

# 2. 复制环境变量模板并填入你的 key
cp .env.example .env
$EDITOR .env

# 3. 跑 lesson 0002 的 hello trace
uv run hello_trace.py

# 4. 去 Langfuse UI 看 trace
open http://localhost:3000
```

## 当前实验

| Lesson | 文件 | 状态 |
|---|---|---|
| 0001 | — | Langfuse 部署（参考 `../cicd/`） |
| 0002 | `hello_trace.py` | ✅ 完成 |
| 0003 | `apps/fuse/react_agent.py` | ✅ 完成 |
| 0004 | `apps/fuse/prompt_demo.py` | ✅ 完成 |
| 0005·1 | `lessons/0005-01-scoring-fundamentals.html` | ✅ 完成 |
| 0005·2 | `apps/fuse/scoring_demo.py` | ✅ 完成 |
| 0005·3 | `apps/fuse/user_feedback_demo.py` | ✅ 完成 |
| 0006+ | 待添加 | — |

## 常用命令

```bash
uv add <package>          # 加新依赖
uv sync                   # 装/同步依赖
uv run <script.py>        # 在项目 venv 里跑脚本
uv run python             # 进入项目 Python REPL
uv run ruff check .       # lint
uv run ruff format .      # format
```

## 跟 Langfuse 部署的关系

这个 `lab/` 目录假设你已经在父目录或别处跑起了 Langfuse 实例（参考
`../lessons/0001-deploy-langfuse-locally.html`）。SDK 通过 `.env` 里的
`LANGFUSE_HOST` 连接到那个实例。