# 自托管只需 docker-compose.yml，不用 clone 整个 langfuse 仓库

用户主动指出：clone 整个仓库会拉几千行无关代码，应该只取 `docker-compose.yml`。已确认这不影响功能——compose 文件是自包含的，只引用 Docker Hub 上的镜像（`langfuse/langfuse:latest`、`postgres`、`redis`、`minio`）。

**Why:** 决定了 lesson 0001 的部署步骤应该是"curl 一个文件 + 启动"，而不是"git clone + 启动"。

**How to apply:**
- 教学/文档中提到任何"用官方 compose"时，优先 `curl https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml`，避免引导 clone。
- 学习阶段推荐**精简版 compose**（只有 postgres，没有 redis / minio），生产场景再补。
- 精简版的取舍：能跑 Trace + Prompt + Eval；不能跑大 payload 上传（>几十 KB）和多媒体上传。

**Evidence:** 用户在 lesson 1 完成后立即反馈 `docker compose 启动 我不需求pull 整个项目吧`。