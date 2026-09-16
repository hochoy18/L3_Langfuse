# 公网多服务 TCP 暴露方案（PostgreSQL + Redis + Milvus）

> 域名：`bananahochoy.online` 的子域名，统一通过 Cloudflare + nginx stream 4 层代理对外暴露。
> 文档版本：2026-09-16

---

## 0. 文档结构

1. [关键评估与风险](#1-关键评估与风险)
2. [Cloudflare 配置](#2-cloudflare-配置)
3. [服务端架构与文件结构](#3-服务端架构与文件结构)
4. [nginx 改造](#4-nginx-改造)
5. [stream 配置文件（PG / Redis / Milvus）](#5-stream-配置文件pg--redis--milvus)
6. [容器侧改造（start.sh / compose）](#6-容器侧改造startsh--compose)
7. [安全加固清单](#7-安全加固清单)
8. [客户端连接示例](#8-客户端连接示例)
9. [落地步骤（按顺序）](#9-落地步骤按顺序)
10. [待确认决策与备选方案](#10-待确认决策与备选方案)

---

## 1. 关键评估与风险

### 1.1 三个服务的"危险等级"差异

| 服务 | 默认认证 | 公网暴露风险 | 必须做 |
|---|---|---|---|
| **PostgreSQL** | 有 user/password | 中 | 强密码、SSL（可选） |
| **Redis** | **默认无密码** | **极高**（史上被黑挖矿第一名） | 强密码 + 命令重命名 + IP 白名单 |
| **Milvus** | **默认无认证** | 高（数据就是向量库本体） | 必须开 auth |

### 1.2 架构性约束：必须走 Cloudflare Spectrum

Cloudflare 的 HTTP 代理（橙色云 ☁️）**只代理 HTTP/HTTPS**。PG/Redis/Milvus 全是私有 TCP 协议，HTTP 代理接不住。所以只有两条路：

| 方案 | 隐藏源站 IP | 防 DDoS | TLS 边缘终止 | 费用 |
|---|---|---|---|---|
| **A. Cloudflare Spectrum** | ✅ | ✅ | ✅ | 💰 **Pro 计划起，~$1/GB 流量** |
| **B. Cloudflare 灰云（DNS-only）** | ❌ 源 IP 全暴露 | ❌ | ❌ | 免费 |

> ⚠️ **强烈建议 A 方案**。B 方案等于把 PG/Redis/Milvus 直接挂在公网上，仅靠密码防。Redis 在公网上几乎活不过 24 小时，会被自动化的扫描器攻破做挖矿肉鸡。

**本文档默认走 A（Spectrum）。** 如果走 B，见 §10.2。

### 1.3 端口与域名规划

| 服务 | 容器本地端口 | 公网边缘端口 | 子域名 |
|---|---|---|---|
| PostgreSQL | 127.0.0.1:5432 | **25432** | `pg.bananahochoy.online` |
| Redis | 127.0.0.1:6379 | **26379** | `redis.bananahochoy.online` |
| Milvus gRPC | 127.0.0.1:19530 | **29530** | `milvus.bananahochoy.online` |
| Milvus metrics | 127.0.0.1:9091 | 不暴露 | - |

> 用 25432 / 26379 / 29530 而非 5432/6379/19530，是因为这些端口客户端通过 Cloudflare Spectrum 连接时是边缘端口。挑不常见的端口能降低被全网扫描器发现和爆破的概率。

---

## 2. Cloudflare 配置

### 2.1 DNS 记录（灰云）

进 Cloudflare 控制台 → `bananahochoy.online` → DNS → Records，加 3 条 A 记录，**代理状态都选「仅 DNS」（灰色云）**：

```
Type  Name        Content            Proxy
A     pg          43.156.165.98      DNS only
A     redis       43.156.165.98      DNS only
A     milvus      43.156.165.98      DNS only
```

> 源站 IP `43.156.165.98` 是示例，请按你机器实际公网 IP 替换。

### 2.2 Spectrum 配置（关键）

Cloudflare 控制台 → **Spectrum** → 添加应用：

| 应用 | 协议 | 边缘端口 → 源端口 | TLS | Origin |
|---|---|---|---|---|
| `pg.bananahochoy.online` | TCP | `25432 → 25432` | Full | `43.156.165.98:25432` |
| `redis.bananahochoy.online` | TCP | `26379 → 26379` | Full | `43.156.165.98:26379` |
| `milvus.bananahochoy.online` | TCP | `29530 → 29530` | Full | `43.156.165.98:29530` |

- **TLS 选 Full**：Cloudflare 边缘 → 客户端用 TLS；边缘 → 源站之间可以明文（这是你自己的网络，没必要再套 TLS）。
- 可选：打开 `PROXY_PROTOCOL` 以记录真实客户端 IP。

---

## 3. 服务端架构与文件结构

```
/opt/infrastructure/nginx/
├── nginx.conf                       # 改：加 stream{} 块
├── conf.d/
│   ├── 3x.conf                      # 不动
│   ├── dsh.conf                     # 不动
│   ├── default.conf                 # 不动
│   ├── zzz.conf                     # 不动
│   └── stream.d/                    # 新建目录
│       ├── pg.conf                  # 改写
│       ├── redis.conf               # 新增
│       └── milvus.conf              # 新增
└── certs/                           # 不动

/home/hochoy/softwares/
├── postgre/                         # 已存在
│   ├── start.sh                     # 改：强密码 + SSL 证书生成
│   └── pgdata/                      # 持久化数据
├── redis/                           # 新建
│   ├── start.sh                     # 新建
│   ├── conf/redis.conf              # 自动生成
│   └── data/                        # 持久化
└── milvus/                          # 新建
    └── docker-compose.yml           # 新建
```

---

## 4. nginx 改造

### 4.1 文件：`/opt/infrastructure/nginx/nginx.conf`

加 `stream {}` 块（**和 `http {}` 平级，不能嵌套**）：

```nginx
user nginx;
worker_processes auto;

events {
    worker_connections 1024;
}

stream {
    log_format stream_main '$remote_addr [$time_local] '
                           '$protocol $status $bytes_sent $bytes_received '
                           '$session_time "$upstream_addr"';
    access_log /var/log/nginx/stream.access.log stream_main;

    include /etc/nginx/conf.d/stream.d/*.conf;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile        on;
    tcp_nopush      on;
    keepalive_timeout 65;

    server_tokens off;

    include /etc/nginx/conf.d/*.conf;
}
```

**关键点**：
- `stream {}` 不能嵌套在 `http {}` 内，否则 `server{}` 会被当成 HTTP server 报 `unknown directive "proxy_pass"` 之类的错。
- `access_log` 路径 `/var/log/nginx/` 是 `nginx:alpine` 镜像默认日志目录，`network_mode: host` 下写入无问题。
- 现有 `include /etc/nginx/conf.d/*.conf;`（http）保持不动，stream 走 `stream.d/`。

### 4.2 创建 stream.d 目录

```bash
mkdir -p /opt/infrastructure/nginx/conf.d/stream.d
```

---

## 5. stream 配置文件（PG / Redis / Milvus）

### 5.1 `/opt/infrastructure/nginx/conf.d/stream.d/pg.conf`

```nginx
upstream pg_backend {
    server 127.0.0.1:5432;
    keepalive 32;          # 长连接池，避免 PG 端短连接风暴
}

server {
    listen 25432;
    proxy_pass pg_backend;

    proxy_timeout         600s;
    proxy_connect_timeout 10s;
    proxy_buffer_size     16k;
}
```

### 5.2 `/opt/infrastructure/nginx/conf.d/stream.d/redis.conf`

```nginx
upstream redis_backend {
    server 127.0.0.1:6379;
    keepalive 32;
}

server {
    listen 26379;
    proxy_pass redis_backend;

    proxy_timeout         600s;
    proxy_connect_timeout 10s;
    proxy_buffer_size     16k;
}
```

### 5.3 `/opt/infrastructure/nginx/conf.d/stream.d/milvus.conf`

```nginx
upstream milvus_grpc {
    server 127.0.0.1:19530;
    keepalive 32;
}

server {
    listen 29530;
    proxy_pass milvus_grpc;

    proxy_timeout         600s;
    proxy_connect_timeout 10s;
    proxy_buffer_size     16k;
}

# 9091 metrics 端口故意不暴露，公网不要碰
# 如需远程看 metrics，建议本地走 SSH 端口转发
```

### 5.4 可选：IP 白名单（如客户端 IP 固定）

在 `server {}` 里加：

```nginx
server {
    listen 25432;

    # 只允许特定 IP 段
    # allow 1.2.3.0/24;
    # allow 1.2.3.4;
    # deny  all;

    proxy_pass pg_backend;
    ...
}
```

---

## 6. 容器侧改造（start.sh / compose）

### 6.1 PostgreSQL：改 `/home/hochoy/softwares/postgre/start.sh`

保留 `-p 127.0.0.1:5432:5432`（只对宿主机回环暴露），增加强密码生成与 SSL 证书生成：

```bash
#!/bin/bash

# 配置
PG_NAME="pg-16"
PG_USER="admin"
# ★ 用 openssl 生成强密码（首次启动时随机生成，存到 .pgpass）★
PG_PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
PG_DB="xui"
PG_DATA="/home/hochoy/softwares/postgre/pgdata"
PG_CERTS="$PG_DATA/certs"

mkdir -p "$PG_DATA"

# 第一次启动前生成自签 SSL 证书（可选，但推荐）
if [ ! -f "$PG_CERTS/server.crt" ]; then
    mkdir -p "$PG_CERTS"
    openssl req -new -x509 -days 3650 -nodes \
        -out "$PG_CERTS/server.crt" \
        -keyout "$PG_CERTS/server.key" \
        -subj "/CN=pg.bananahochoy.online"
    chmod 600 "$PG_CERTS/server.key"
fi

# 已有密码文件就复用（避免容器每次重启改密码）
if [ -f "$PG_DATA/.pgpass" ]; then
    PG_PASS="$(cat $PG_DATA/.pgpass)"
fi

docker run -d \
  --name "$PG_NAME" \
  --restart unless-stopped \
  -e POSTGRES_USER="$PG_USER" \
  -e POSTGRES_PASSWORD="$PG_PASS" \
  -e POSTGRES_DB="$PG_DB" \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v "$PG_DATA:/var/lib/postgresql/data" \
  -p 127.0.0.1:5432:5432 \
  postgres:16-alpine

# 把生成的密码保存到文件，方便你取
echo "$PG_PASS" > "$PG_DATA/.pgpass"
chmod 600 "$PG_DATA/.pgpass"

echo "PostgreSQL 已启动"
echo "密码: cat $PG_DATA/.pgpass"
echo "DSN: postgres://$PG_USER:$PG_PASS@127.0.0.1:5432/$PG_DB?sslmode=disable"
```

> 若想让客户端强制 SSL，进容器启用：
> ```bash
> docker exec -it pg-16 bash
> cat >> /var/lib/postgresql/data/pgdata/postgresql.conf <<'EOF'
> ssl = on
> ssl_cert_file = 'certs/server.crt'
> ssl_key_file = 'certs/server.key'
> EOF
> exit
> docker restart pg-16
> ```
> 但如果已经走 Cloudflare Spectrum TLS，可保持明文省事。

### 6.2 Redis：新建 `/home/hochoy/softwares/redis/start.sh`

```bash
#!/bin/bash

REDIS_NAME="redis-7"
REDIS_DATA="/home/hochoy/softwares/redis/data"
REDIS_CONF_DIR="/home/hochoy/softwares/redis/conf"
# ★ 强密码（自动生成，存到 .redispass）★
REDIS_PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"

mkdir -p "$REDIS_DATA" "$REDIS_CONF_DIR"

# 持久化配置
cat > "$REDIS_CONF_DIR/redis.conf" <<EOF
bind 127.0.0.1
protected-mode yes
port 6379
daemonize no

# 认证（必须）
requirepass $REDIS_PASS

# 持久化
appendonly yes
appendfsync everysec

# 网络优化
tcp-keepalive 60
timeout 300

# 安全：重命名/禁用危险命令
rename-command CONFIG ""
rename-command DEBUG ""
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command KEYS ""
rename-command SHUTDOWN ""
rename-command ACL ""
EOF

docker run -d \
  --name "$REDIS_NAME" \
  --restart unless-stopped \
  -v "$REDIS_DATA:/data" \
  -v "$REDIS_CONF_DIR/redis.conf:/usr/local/etc/redis/redis.conf:ro" \
  -p 127.0.0.1:6379:6379 \
  redis:7-alpine redis-server /usr/local/etc/redis/redis.conf

echo "$REDIS_PASS" > "$REDIS_DATA/.redispass"
chmod 600 "$REDIS_DATA/.redispass"
echo "Redis 密码: cat $REDIS_DATA/.redispass"
echo "连接: redis-cli -h 127.0.0.1 -a \$(cat $REDIS_DATA/.redispass)"
```

### 6.3 Milvus：新建 `/home/hochoy/softwares/milvus/docker-compose.yml`

Milvus standalone 单容器版（自带 etcd + MinIO）：

```yaml
services:
  milvus:
    container_name: milvus
    image: milvusdb/milvus:v2.4.10
    restart: unless-stopped
    command: ["milvus", "run", "standalone"]
    ports:
      - "127.0.0.1:19530:19530"   # gRPC（对外）
      - "127.0.0.1:9091:9091"     # metrics（仅本地）
    environment:
      # ★ 必须开 auth ★
      MILVUS_AUTHORIZATION_ENABLED: "true"
      MILVUS_USR: "admin"
      MILVUS_PWD: "改成强密码"     # ★ 改成强密码后再启动 ★
    volumes:
      - ./data:/var/lib/milvus
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
```

启动：

```bash
mkdir -p ~/softwares/milvus && cd ~/softwares/milvus
docker compose up -d
```

> **资源警告**：Milvus standalone 启动后常驻约 **1.5~2 GB RAM**，MinIO 还要吃一点磁盘 IO。先确认机器资源够用。

---

## 7. 安全加固清单

| # | 动作 | 必要性 |
|---|---|---|
| 1 | 改 PG 默认密码 `admin#123` | **必须** |
| 2 | Redis 必须设 `requirepass` | **必须** |
| 3 | Milvus 必须开 `MILVUS_AUTHORIZATION_ENABLED` | **必须** |
| 4 | Redis 重命名/禁用 `CONFIG`、`FLUSHALL` 等危险命令 | **必须** |
| 5 | Cloudflare Spectrum 启用 TLS（Full） | **必须** |
| 6 | 在 Cloudflare Spectrum 里把 `PROXY_PROTOCOL` 打开，记录真实客户端 IP | 推荐 |
| 7 | 云厂商安全组只放行 80/443（HTTP 站点用）+ 25432/26379/29530 给已知 IP 段 | 推荐 |
| 8 | nginx stream `allow/deny` IP 白名单（客户端 IP 固定时） | 推荐 |
| 9 | PG 启用 SSL + 客户端 `sslmode=require` | 推荐 |
| 10 | Redis 启用原生 TLS（`--tls-mode yes`），客户端用 `rediss://` | 可选 |
| 11 | 在所有容器配 fail2ban / 简单的连接频次限制 | 可选 |

---

## 8. 客户端连接示例

```bash
# PostgreSQL（明文，走 Cloudflare Spectrum 边缘 TLS）
psql "host=pg.bananahochoy.online port=25432 user=admin dbname=xui"

# Redis（带密码）
redis-cli -h redis.bananahochoy.online -p 26379 -a '强密码'

# Milvus（Python）
from pymilvus import connections
connections.connect(
    alias="default",
    host="milvus.bananahochoy.online",
    port="29530",
    user="admin",
    password="强密码",
)
```

---

## 9. 落地步骤（按顺序）

| 步骤 | 内容 | 风险 |
|---|---|---|
| ① | 在 Cloudflare 加 3 条 A 记录（灰云） | 无 |
| ② | Cloudflare Spectrum 加 3 个应用 | 无 |
| ③ | 改 `nginx.conf` 加 `stream{}` 块 | 无 |
| ④ | 创建 `conf.d/stream.d/` 目录 | 无 |
| ⑤ | 写 3 个 `stream.d/*.conf` 文件 | 无 |
| ⑥ | `docker exec nginx nginx -t` 验证语法 | 无 |
| ⑦ | `docker exec nginx nginx -s reload` | 短暂不影响 HTTP 业务 |
| ⑧ | `docker exec -it nginx ss -tlnp` 确认 25432/26379/29530 在 listen | 无 |
| ⑨ | 本机 `psql -h 127.0.0.1 -p 5432 -U admin` 验证 PG 本身 OK | 无 |
| ⑩ | 部署 Redis 容器，验证本机 `redis-cli` | 无 |
| ⑪ | 部署 Milvus 容器，验证本机 pymilvus 连 19530 | 无 |
| ⑫ | 改所有默认密码 | **必须先做再开公网** |
| ⑬ | 从外部用 `pg.bananahochoy.online:25432` 等域名 + 端口测试连接 | 正式开公网 |
| ⑭ | 打开 Cloudflare Spectrum 启用开关 | 正式开公网 |
| ⑮ | 加 IP 白名单 / 监控告警 | 加固 |

---

## 10. 待确认决策与备选方案

### 10.1 已建议的默认值

| 决策项 | 建议值 |
|---|---|
| Cloudflare 计划 | **Spectrum**（唯一安全选项） |
| Milvus 客户端认证 | `MILVUS_USR/PWD`（简单） |
| Milvus 是否暴露 Web UI（Attu） | 不暴露（HTTP 服务需另开方案） |
| 公网端口 | 25432 / 26379 / 29530 |

### 10.2 备选：DNS-only 直连方案（不推荐）

若不走 Spectrum：

- DNS 仍然 3 条 A 记录灰云
- **不做 Spectrum**
- 源站防火墙 / 云安全组必须**只放行你客户端的 IP**（白名单），否则 0 防护
- 强密码 + 命令限制 + IP 白名单 三件套必须全部到位

适合场景：临时测试 / 客户端 IP 完全固定且数量少。

### 10.3 备选：给 Milvus 加 Web UI（Attu）

如需 Web UI 管理 Milvus：

- Attu 是独立 HTTP 服务，默认 3000 端口
- 在 `stream.d/` 之外另开 `http {}` 服务，走 `milvus-ui.bananahochoy.online`
- Cloudflare DNS 用**橙色云**（HTTP 代理）
- 需要单独写一个 `conf.d/milvus-ui.conf` 的 `server{}`，里面 `proxy_pass http://127.0.0.1:3000;`

本期方案不做，留作后续扩展。

---

## 附录 A：当前 nginx 现状速查

### A.1 `/opt/infrastructure/nginx/nginx.conf`（现状）

```nginx
user nginx;

worker_processes auto;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;

    server_tokens off;

    include /etc/nginx/conf.d/*.conf;
}
```

→ 需要在 `events {}` 后、`http {}` 前插入 `stream {}` 块（见 §4.1）。

### A.2 `/opt/infrastructure/nginx/conf.d/pg.conf`（现状，错误）

整文件是 HTTP `server{}` + `proxy_pass https://127.0.0.1:5432;` 的写法，**协议错配**（PG 不是 HTTP）。需要整文件改写为 stream 语法（见 §5.1），并移到 `conf.d/stream.d/pg.conf`。

### A.3 `/opt/infrastructure/nginx/compose.yml`（现状）

```yaml
services:
  nginx:
    image: nginx:alpine
    container_name: nginx
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./conf.d:/etc/nginx/conf.d:ro
      - ./certs:/etc/nginx/certs:ro
```

→ 不需要改。`nginx:alpine` 默认包含 `--with-stream` 模块，`stream{}` 可用。`network_mode: host` + 卷挂载 `./conf.d` 已自动覆盖 `stream.d/` 子目录。

### A.4 `/home/hochoy/softwares/postgre/start.sh`（现状）

```bash
#!/bin/bash

PG_NAME="pg-16"
PG_USER="admin"
PG_PASS="admin#123"      # ← 务必改成强密码
PG_DB="xui"
PG_DATA="/home/hochoy/softwares/postgre/pgdata"

mkdir -p "$PG_DATA"

docker run -d \
  --name "$PG_NAME" \
  --restart unless-stopped \
  -e POSTGRES_USER="$PG_USER" \
  -e POSTGRES_PASSWORD="$PG_PASS" \
  -e POSTGRES_DB="$PG_DB" \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v "$PG_DATA:/var/lib/postgresql/data" \
  -p 127.0.0.1:5432:5432 \
  postgres:16-alpine

echo "PostgreSQL 已启动"
echo "DSN: postgres://$PG_USER:$PG_PASS@127.0.0.1:5432/$PG_DB?sslmode=disable"
```

→ 主要问题：默认密码 `admin#123` 弱、需要换成自动生成；可选加 SSL 证书生成。详见 §6.1。
