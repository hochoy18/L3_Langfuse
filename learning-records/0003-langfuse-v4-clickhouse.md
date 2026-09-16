# Langfuse v4 架构变化：ClickHouse 加入栈中

Langfuse v4（对比 v3）把 analytics-heavy 部分（trace / observation 的高吞吐 OLAP 查询）从 postgres 迁移到了 **ClickHouse**。原来的架构是 `postgres + redis + minio`，v4 变成 `postgres + clickhouse + redis + minio`：

- **postgres**：仍然存 metadata（用户、项目、prompt、score、dataset 定义、API key）
- **clickhouse**：新加入，存 trace / observation / log 行，专门处理高吞吐写入 + 时间范围聚合查询
- **redis**：队列 + 速率限制（不变）
- **minio / S3**：blob 存储（不变）

**Why:** 用户的 lesson 1 部署在 pull `clickhouse-server:25.12`——说明他在用官方 v4 compose（方案 A），不是 lesson 里写的精简版。

**How to apply:**
- lesson 0001 提到的"精简版（只有 postgres）"对 v4 不再适用——v4 web 容器启动时会校验 ClickHouse 连通性，缺了就 fail。
- 如果要给学习者写一个精简 compose，必须包含 postgres + clickhouse 至少这两件；redis + minio 仍可省（影响批量 / 媒体上传）。
- 讲解 trace 流程时强调"trace 行先入 redis 队列，再异步批量写入 clickhouse"，有助于理解为什么 web 重启不会丢数据。

**Evidence:** 用户截图显示正在拉 6 个 image，其中 `clickhouse-server:25.12` 是 v4 标志。