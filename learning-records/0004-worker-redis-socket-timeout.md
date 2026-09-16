# v4 自托管典型坑：worker 报 Redis Socket timeout

Langfuse v4 worker 容器在所有 queue job 上持续报 `Socket timeout. Expecting data, but didn't receive any in 30000ms`，看似 redis 不可达。实际原因通常有 3 层：

1. **Redis 容器 OOMKilled**（Docker Desktop 默认内存不足，6 容器同时跑时 redis 最先被杀）
2. **Redis 启动未就绪**（health check 显示 healthy，但实际命令处理还有延迟）
3. **Docker 网络 DNS 解析**（worker 容器拿不到 `redis` 这个服务名）

**Why:** 这是部署 lesson 没法在文档里说清的"运行时坑"，会在用户第一次自托管时几乎必然出现。

**How to apply:**
- lesson 0001 部署步骤必须包含 `docker compose ps` 检查环节，不能只说"看到 Ready 就 OK"
- lesson 0001 应明示 Docker Desktop 内存调至 ≥ 12 GiB（v4 时代，6 容器同时跑）
- 排错顺序：先 `docker compose ps` 看容器状态 → 看 OOMKilled → 看 redis 日志 → 进 worker 容器 `nc -zv redis 6379` 测连通性
- 提供"快速绕过"环境变量 `LANGFUSE_SKIP_QUEUE_WORKER=true` 给只是想看 UI 的初学者

**Evidence:** 用户在部署完成尝试注册时报告 10+ 条 worker queue job 全部 Socket timeout。