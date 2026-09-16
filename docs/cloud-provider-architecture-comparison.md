# 云厂商数据库架构 vs 我们的方案（AWS / 阿里云 / Cloudflare 对照）

> 目的：理解我们当前方案在行业里处于什么位置，可以借鉴哪些实践。
> 文档版本：2026-09-16

---

## 1. 核心结论

云厂商（AWS、阿里云、GCP 等）托管数据库的思路**本质和我们一样**，都是三件套：

```
[客户端]
   │  (1) 网络隔离
   ↓
[防火墙 / 安全组 / 白名单]
   │  (2) 认证
   ↓
[数据库进程 + TLS]
```

不同点在于**网络隔离的具体实现**：云厂商用 VPC（虚拟私有云），我们用 `127.0.0.1` + nginx stream + Cloudflare Spectrum 模拟出类似效果。

---

## 2. 云厂商防护层对照表

| 防护层 | AWS | 阿里云 | 我们这次的方案 |
|---|---|---|---|
| 网络隔离 | **VPC 私有子网**，DB 没有公网 IP | **专有网络 VPC**，白名单模式 | `127.0.0.1` 绑定 + nginx stream |
| 防火墙 | Security Group（状态化） | 安全组 / IP 白名单 | nginx stream `allow/deny` + 云厂商安全组 |
| 认证 | IAM + DB user/password | RAM + 账号密码 | PG user/pwd、Redis `requirepass`、Milvus `MILVUS_USR/PWD` |
| TLS | RDS CA bundle，免费托管证书 | 阿里云 CA，免费 | Cloudflare Spectrum 边缘 TLS |
| 监控 | CloudWatch 指标 + 慢日志 | CloudMonitor + 审计日志 | nginx stream access_log（自建） |
| 中间代理 | **没有**（客户端直连 DB） | **没有**（客户端直连 DB） | **有**（nginx stream 多一跳） |

---

## 3. 与我们方案最关键的 3 个差别

### 3.1 云厂商不用中间代理层

AWS RDS / 阿里云 RDS 都是**客户端直连 DB 实例**。中间没有任何 nginx / haproxy / envoy。

**原因**：

- **一跳比两跳快**：少一次 TCP 握手、少一次内核态切换
- **少一个故障点**：nginx 挂了 = 整个 DB 不可达
- DB 本身已经足够稳，自己能处理成千上万并发连接

我们加 nginx stream 是**没有 VPC 抽象的折中**——用 nginx 在一台机器上模拟出"内网 DB + 公网代理"的隔离效果。对低流量个人/小团队完全够用，但**不是云厂商的做法**。

**贴近云厂商的穷人版方案**：

不开公网端口，本地应用 → 走 `127.0.0.1:5432` 直连；远程管理 → **SSH 端口转发**：

```bash
ssh -L 25432:127.0.0.1:5432 user@server
# 然后本地：
psql -h 127.0.0.1 -p 25432 -U admin -d xui
```

这其实就是云厂商「私有连接」(AWS PrivateLink / 阿里云 私网连接) 的穷人版。

### 3.2 云厂商的网络隔离是「真正」的网络隔离

云厂商用 **VPC**：DB 跑在虚拟私有网络的私有子网里，**根本没有公网 IP**，物理上无法从公网触达。

我们用 `127.0.0.1` + nginx stream + Cloudflare Spectrum 模拟了类似效果，但前提是：

- Cloudflare Spectrum 工作正常（域名解析走 CF、流量先到 CF 边缘）
- 如果有人知道了你的源站 IP（`43.156.165.98`），**他可以直接绕过 Cloudflare**，绕过 nginx stream，直接打你机器的 `25432/26379/29530` 端口
- 所以我们额外依赖**云厂商安全组**作为最后一道墙

云厂商的 VPC 隔离**不存在这种绕过问题**，因为根本没有公网 IP 可打。

### 3.3 TLS 处理方式不同

| | 云厂商 | 我们 |
|---|---|---|
| 证书 | 由云厂商签发 + 自动轮换 | 自己生成 / 用 Cloudflare |
| 端到端加密 | 客户端 ↔ DB 全程 TLS（in-transit encryption） | 客户端 ↔ CF 边缘 TLS，CF ↔ 源站 **明文**（在自己的网络里） |
| 配置复杂度 | 控制台勾一下 | 要自己部署、轮换 |

我们的"CF 边缘 TLS + 源站明文"是合理选择（节省性能 + 简化运维），但**严格说不算端到端加密**。如果合规要求"客户端到 DB 全程 TLS"，需要再加一层（PG 原生 SSL 或 stunnel）。

---

## 4. Cloudflare Spectrum 在行业的位置

| 能力 | AWS | Cloudflare | 阿里云 |
|---|---|---|---|
| 公网暴露 TCP 服务，带 DDoS 防护 | PrivateLink + Shield | **Spectrum** | 对个人/小企业暂无完整方案 |
| 隐藏源站 IP | ✅ | ✅ | ✅（VPC 天然） |
| 边缘 TLS 终止 | ✅（CloudFront） | ✅ | ✅ |
| 全球 Anycast | ✅ | ✅ | 有限 |

**Spectrum 的优势**：

- 隐藏源站 IP
- 全球 DDoS 防护
- TLS 边缘终止
- 性价比远高于 AWS Shield + Global Accelerator

对个人 / 中小项目，**Spectrum 是当前最实用的"穷人版企业级 DDoS 防护"**。

---

## 5. 给我们的几点启示

### 5.1 当前方案评估

- ✅ **小流量场景下完全够用**，不需要过度焦虑
- ✅ 三件套（隔离 + 认证 + 加密）凑齐了
- ⚠️ nginx stream 这一跳是单点故障（如果 nginx 挂了，所有 DB 不可达）
- ⚠️ 源站 IP 一旦泄露，所有流量绕过 Cloudflare 直接打到机器

### 5.2 后续可演进的方向

按从简到繁的演进路线：

1. **当前**：`127.0.0.1` + nginx stream + Cloudflare Spectrum
2. **流量增大后**：去掉 nginx stream，容器 bind 到 `0.0.0.0` 或特定非 loopback IP，安全组白名单
3. **生产规模**：上 VPS 多机、VPC、安全组 + Cloudflare Spectrum 多重防护
4. **超大规模**：直接用云厂商托管服务（RDS / ElastiCache / 向量数据库托管版）

### 5.3 云厂商也在 DB 前加代理，但目的不同

AWS RDS Proxy、阿里云 RDS 读写分离代理，**不是因为 DB 不稳**，而是为了：

- **连接池**：避免每个应用连接都新建 TCP 连接，吃光 DB 的 `max_connections`
- **读写分离**：路由 SELECT 到只读副本
- **故障切换**：代理层做健康检查，主备切换对应用透明

我们在 nginx stream 里加的 `keepalive 32`，目的和 RDS Proxy 是**一样的**——减少 TCP 连接建立的开销。这是值得学习的细节。

---

## 6. 生产环境的"标准答案"

无论云厂商还是自建，标准防护都是这五件套：

| # | 项 | 云厂商 | 我们 |
|---|---|---|---|
| 1 | 网络隔离 | VPC 私有子网 | `127.0.0.1` + CF Spectrum |
| 2 | 防火墙 | Security Group | nginx stream `allow/deny` + 云厂商安全组 |
| 3 | 强认证 | IAM + DB 密码 | DB 密码（已实现） |
| 4 | 传输加密 | TLS 全程 | TLS（CF 边缘） |
| 5 | 审计日志 | CloudTrail / CloudMonitor | nginx access_log（凑合） |

我们能凑齐前 4 项，第 5 项用 nginx stream access_log 凑合，已经接近一个**最小可用的生产防护**。

---

## 7. 一句话总结

> 云厂商的本质是「**网络隔离 + 认证 + 加密**」三件套；我们没有 VPC，所以用 nginx stream + 127.0.0.1 + Cloudflare Spectrum **在单台机器上模拟出同样的隔离效果**。思路一样，工具不同，规模不同。

---

## 附录：术语速查

- **VPC**（Virtual Private Cloud）：云厂商提供的虚拟私有网络。在 VPC 里的资源默认没有公网 IP，只能从 VPC 内或通过 peering/PrivateLink 访问。
- **Security Group**：AWS 的状态化防火墙，按协议/端口/源 IP 规则控制进出。
- **PrivateLink**：AWS 提供的私有连接服务，让不同 VPC 之间通过内网互通。
- **Cloudflare Spectrum**：Cloudflare 的 TCP/UDP 代理服务，把任意 TCP/UDP 流量代理到源站，附带 DDoS 防护和 TLS。
- **RDS Proxy**：AWS RDS 的连接池代理，介于应用和 RDS 之间。
- **Connection pooling**：连接池，复用 TCP 连接减少握手开销。
