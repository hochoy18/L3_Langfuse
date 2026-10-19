# Langfuse 自部署指南（Postgres + Redis + ClickHouse + Cloudflare R2）

> Langfuse v4 自部署栈。PostgreSQL / Redis / ClickHouse 走本地 Docker，
> 对象存储走 **Cloudflare R2**（零出口费 + 比 MinIO 少一个容器）。

---

## 0. 架构一览

```
┌─ Docker Compose（langfuse-deploy/）────────────────────────┐
│                                                            │
│  langfuse-web :3000  ─┐                                   │
│                       ├─→  ┌──────────┐                   │
│  langfuse-worker      ─┤    │ Postgres │ 5432 (本地)       │
│                       │    └──────────┘                   │
│  clickhouse :8123 ────┤    ┌──────────┐                   │
│                       ├─→  │   Redis  │ 6379 (本地)       │
│                       │    └──────────┘                   │
│                       │    ┌────────────────────┐         │
│                       └─→  │   ClickHouse :8123  │         │
│                            └────────────────────┘         │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼ HTTPS（公网）
                    ┌──────────────────────┐
                    │  Cloudflare R2       │
                    │  - events/ prefix    │
                    │  - media/ prefix     │
                    │  - exports/ prefix   │
                    └──────────────────────┘
```

> ClickHouse **必须本地部署**——目前没有兼容 Langfuse v4 的托管服务。
> PostgreSQL / Redis 可后续换成 Supabase / Upstash（本期未做）。

---

## 1. 一次性准备：Cloudflare R2

### 1.1 建桶（建议 1 个桶 + 3 prefix）

1. 登录 Cloudflare 控制台 → 左侧 **R2 → Object Storage**
2. 点 **Create bucket**
   - **Bucket name**：`langfuse`（或任意名，记下，后面填 `.env`）
   - **Location**：Automatic
3. 点进桶 → **Settings** 标签，记下 **Bucket name**
4. （可选）建 3 个 prefix 子目录：`events/` `media/` `exports/`

> 也可以建 3 个独立桶 `langfuse-events` / `langfuse-media` / `langfuse-exports`，
> 各自独立配权限和生命周期。**推荐单桶 + prefix**，管理成本低。

### 1.2 建 API Token

1. R2 概览页右上角 → **Manage R2 API Tokens** → **Create API Token**
2. 配置：

   | 字段 | 值 |
   |---|---|
   | **Token name** | `langfuse-deploy`（任意标识）|
   | **Permissions** | **Object Read & Write** |
   | **TTL** | Forever（或自定义过期）|
   | **Bucket scope** | Apply to specific buckets → 选刚才建的桶（选 All buckets 也行）|

3. 点 **Create API Token**
4. **只显示一次**的弹窗，复制保存：
   - **Access Key ID**（`R2_ACCESS_KEY_ID`）
   - **Secret Access Key**（`R2_SECRET_ACCESS_KEY`）
   - **S3 API Endpoint**（默认 `https://<account>.r2.cloudflarestorage.com`，仅参考，compose 用 `${R2_ACCOUNT_ID}` 拼接）

### 1.3 拿 Account ID

R2 概览页右下角 → **Account ID**（32 位 hex 字符串） → 填到 `.env` 的 `R2_ACCOUNT_ID`

---

## 2. 配置 `.env`

```bash
cd langfuse-deploy
cp .env.example .env
# 用编辑器填值（见 .env.example 注释）
```

### 2.1 自动生成强密码（推荐）

```bash
# Postgres / Redis / ClickHouse 密码
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)" >> .env
echo "REDIS_AUTH=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)" >> .env
echo "CLICKHOUSE_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)" >> .env

# Langfuse secrets（必须 hex，64 字符）
echo "SALT=$(openssl rand -hex 32)" >> .env
echo "ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env
```

### 2.2 填 R2 三个值

```bash
R2_ACCOUNT_ID=<R2 概览页右下角 32 位>
R2_ACCESS_KEY_ID=<刚才创建的 Access Key>
R2_SECRET_ACCESS_KEY=<刚才创建的 Secret>
```

### 2.3 填 langfuse 桶名（必须与 R2 控制台一致）

```bash
LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse
LANGFUSE_S3_MEDIA_UPLOAD_BUCKET=langfuse
LANGFUSE_S3_BATCH_EXPORT_BUCKET=langfuse
```

### 2.4 首次启动自动建组织（可选）

```bash
LANGFUSE_INIT_ORG_NAME=My Org
LANGFUSE_INIT_PROJECT_NAME=agent-sandbox
LANGFUSE_INIT_USER_EMAIL=admin@example.com
LANGFUSE_INIT_USER_PASSWORD=<强密码>
LANGFUSE_INIT_USER_NAME=Admin
```

启动后 Langfuse 会自动建好 Organization + Project + Admin User + API Key pair（记下来），
可在 UI 登录。

---

## 3. 启动栈

```bash
cd langfuse-deploy
docker compose up -d
# 等 1-2 分钟（ClickHouse 启动 + Langfuse 跑 migrations）
```

### 3.1 看启动日志

```bash
docker compose logs -f langfuse-web
# 看到 "Ready" / "Server listening" 即 OK
```

### 3.2 验证 5 件事

```bash
# 1. UI 可访问
curl -I http://localhost:3000

# 2. ClickHouse 健康
curl http://localhost:8123/ping
# 期望返回: Ok.

# 3. Postgres 健康
docker exec langfuse-deploy-postgres-1 pg_isready -U postgres
# 期望返回: accepting connections

# 4. Redis 健康
docker exec langfuse-deploy-redis-1 redis-cli ping
# 期望返回: PONG

# 5. Langfuse → R2 连通（看日志确认无 S3 报错）
docker compose logs langfuse-web | grep -i "s3\|r2"
# 期望: 无 ERROR
```

---

## 4. 跑 hello trace 验证

```bash
# 在项目根目录
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse

# 改 .env 指向 127.0.0.1:3000
echo "LANGFUSE_BASE_URL=http://localhost:3000" >> .env
echo "LANGFUSE_PUBLIC_KEY=pk-lf-..." >> .env
echo "LANGFUSE_SECRET_KEY=sk-lf-..." >> .env

# 跑 hello trace
uv run python -m src.hello_trace
# 期望控制台打印 trace id
```

浏览器开 `http://localhost:3000` → 项目 → Tracing → 看到刚才那条 trace。
打开 R2 控制台 → `langfuse/events/` → 应该出现对应对象（Langfuse 把每条 trace 上传成对象）。

---

## 5. 端口与数据流

| 服务 | 本地端口 | 暴露公网？ |
|---|---|---|
| Langfuse Web | 3000 | **是**（你访问 UI 的入口）|
| Langfuse Worker | 3030 | 否（仅容器内调用）|
| Postgres | 5432 | 否（仅容器内）|
| Redis | 6379 | 否 |
| ClickHouse HTTP | 8123 | 否 |
| ClickHouse TCP | 9000 | 否 |
| MinIO | （已删除）| — |

> **安全提醒**：3000 是公网入口，建议在云厂商防火墙只放 80/443 + 3000 给特定 IP。
> 其他端口留 127.0.0.1 即可。

---

## 6. 常见问题排查

### 6.1 `langfuse-web` 一直重启 / CrashLoopBackOff

```bash
docker compose logs langfuse-web --tail=50
```

最常见：

| 报错 | 修法 |
|---|---|
| `DATABASE_URL: ... is invalid` | `.env` 里 `POSTGRES_PASSWORD` 包含特殊字符（如 `#`），要用 URL 编码或换密码 |
| `ECONNREFUSED postgres:5432` | `postgres` 容器没起来，`docker compose ps` 看状态 |
| `ClickHouse: connection refused` | clickhouse 容器没起来，看 healthcheck 是否 healthy |
| `R2_ACCOUNT_ID not set` | `.env` 没填 `R2_ACCOUNT_ID`，填上重启 |

### 6.2 启动后 UI 500 报错

Langfuse web 起来了但 UI 报错——通常是 ClickHouse 没初始化完。
看 `docker compose logs langfuse-web | grep migration` 看迁移日志。

### 6.3 R2 上看不到对象

确认 `.env` 里：
```bash
LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=true  # 必须 true
LANGFUSE_S3_EVENT_UPLOAD_REGION=auto           # 必须 auto
```

且 R2 桶的 **Public Access** 设为 **Limited**（默认就是 Limited，不要开 Public），
API Token 有 **Object Read & Write** 权限。

### 6.4 Redis 连接失败 / BullMQ 报错

```bash
# 看 worker 日志
docker compose logs langfuse-worker | grep -i redis
```

`REDIS_AUTH` 没设或 Redis 容器没 requirepass——`.env` 里 `REDIS_AUTH` 必须和 docker-compose 里的 `redis.command: --requirepass ${REDIS_AUTH}` 一致（已经是一致的，OK）。

### 6.5 ClickHouse OOM 抢 CPU

参见 `learning-records/0004-worker-redis-socket-timeout.md`：
ClickHouse 不限资源会拖崩 Redis。给 clickhouse 加 cpus/memory 限制（在 docker-compose 加 `deploy.resources.limits`）。

---

## 7. 数据备份 / 迁移

### 7.1 Postgres 备份

```bash
docker exec langfuse-deploy-postgres-1 \
  pg_dump -U postgres -d postgres > langfuse_pg_$(date +%Y%m%d).sql
```

### 7.2 ClickHouse 备份

```bash
# ClickHouse 数据卷在 langfuse_clickhouse_data 命名卷
docker run --rm \
  -v langfuse-deploy_langfuse_clickhouse_data:/source:ro \
  -v $(pwd):/backup \
  alpine tar czf /backup/clickhouse_$(date +%Y%m%d).tgz -C /source .
```

### 7.3 R2 数据备份（可选）

R2 本身就有 99.999999999% durability，**不需要单独备份**。
但版本控制 / 误删防护可以用 R2 的 [Object Lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycle/)
设 30 天后归档到 Infrequent Access tier。

---

## 8. 升级 Langfuse 版本

```bash
cd langfuse-deploy
docker compose pull langfuse-web langfuse-worker
docker compose up -d
# 等 ClickHouse 跑 migrations（看 worker 日志）
```

> Langfuse v4 的 image tag 是 `:4`（latest），升级到 minor 版本自动拉。
> 主版本升级（如 v4 → v5）等官方 migration 指南。

---

## 9. 资源建议

| 流量级别 | CPU | RAM | Disk |
|---|---|---|---|
| 学习 / demo | 2 核 | 4 GB | 20 GB |
| 小团队（<100K trace/月）| 4 核 | 8 GB | 100 GB |
| 中等（<1M trace/月）| 8 核 | 16 GB | 500 GB |

> ClickHouse 占大头（trace 时序数据 + observations）。
> Postgres 较小（只存 metadata + users + prompts + datasets）。
> Redis 几乎不占（队列 + 缓存）。

---

## 10. 月费估算（小团队 100K trace/月）

| 服务 | 月费 |
|---|---|
| Langfuse 自部署（你的机器）| 含在服务器开销 |
| Cloudflare R2 | **~$5**（10 GB 存储 + egress 免费）|
| Postgres / Redis / ClickHouse 自部署 | 含在服务器开销 |

总额外开销仅 R2 的 $5/月。比用 MinIO + AWS S3 便宜 10 倍。

---

## 11. 出问题找哪

| 现象 | 查哪 |
|---|---|
| UI 加载慢 / 502 | `docker compose logs langfuse-web` |
| trace 写入慢 | `docker compose logs langfuse-worker` |
| ClickHouse 报错 | `docker compose logs clickhouse` |
| R2 写失败 | Langfuse 日志里搜 `s3` 或 `r2` |
| 性能 / OOM | `docker stats` 看哪个容器吃资源 |
| 项目级常见坑 | 父目录 `learning-records/0003-langfuse-v4-clickhouse.md` |
| R2 替换 MinIO 的设计决策 | 父目录 `docs/cloud-provider-architecture-comparison.md` |

---

## 附录 A：完整 `.env` 模板（复制到 `.env` 后填值）

参见同目录的 `.env.example`。所有 `__XXX__` 占位符都需要替换。
**3 个 R2 变量未填会导致 Langfuse 直接报错退出（compose 用 `${VAR:?...}` 语法强制）**。