# Specification Quality Checklist: Research 阶段（真实 web search + Send 并发 + ReRank）

**Purpose**: 验证 spec 003 在进入 `/speckit-plan` 之前的需求完整性与质量。
**Created**: 2026-10-19
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] **CHK-001** No implementation details（languages / frameworks / APIs）— 实现了。spec 003 提到 LangGraph / DashScope / Langfuse 是**已冻结的依赖**（spec 001 / 002 已锁定在 `pyproject.toml`），不是新引入；Send API / `pg_advisory_xact_lock` / chevron 渲染等"how"仅出现在 NFR 约束段落（与既有 spec 001 / 002 一致），核心 US/FR 用业务语言（"5 个 web search 并发"、"每个 Source 获得 0-1 的 score"、"Researcher 真实验证 web search 通道"）。
- [x] **CHK-002** Focused on user value and business needs — 实现了。5 个 User Story 全部从 Researcher / Lesson 0007 实验脚本 / 节约 token 的实验脚本 视角写"作为 ... 我能 ..."，每个 US 配独立测试 + 验收场景。
- [x] **CHK-003** Written for non-technical stakeholders — 大部分满足。Q1-Q7 Clarifications 是技术决策但写在显式章节，US/FR 部分业务读者可读。NFR-001（latency）可被业务读者理解为"研究阶段不能太慢"。
- [x] **CHK-004** All mandatory sections completed — 实现了。User Scenarios & Testing（5 US + Edge Cases）/ Requirements（24 FR + 4 NFR）/ Success Criteria（10 SC）/ Assumptions / Out of Scope / Dependencies / Notes 全部齐全。

## Requirement Completeness

- [x] **CHK-005** No [NEEDS CLARIFICATION] markers remain — 实现了。spec 003 在 Clarifications 章节前置 7 个 Q&A（Q1 MaaS App 路径 / Q2 ReRank 凭证 / Q3 research LLM role / Q4 变长 fan-out / Q5 LLM Judge role / Q6 Source.index 重编号 / Q7 Send 状态聚合），把架构层决策提前解决，**不**在 FR 段落留 [NEEDS CLARIFICATION] 让 plan 阶段拍板。
- [x] **CHK-006** Requirements are testable and unambiguous — 实现了。24 个 FR 全部是 MUST 行为描述 + 可执行命令（FR-001 调 `Application.call(APP_ID)` / FR-002 Source 字段映射 / FR-006 `TextReRank.call` 参数 / FR-013 条件边判据 / FR-014 `+1` 时机 / FR-019 prompt 占位符表）。
- [x] **CHK-007** Success criteria are measurable — 实现了。10 个 SC 全部含 `python -c "..."` 命令 + `psql` 查询 + 文件路径，每条可由 Lesson 0007 实验脚本机械化跑。SC-002 的 0.5 秒并发阈值、SC-004 的 counter 范围、SC-009 的 footgun 防御、SC-010 的 fallback 静默都是明确可量化。
- [x] **CHK-008** Success criteria are technology-agnostic — **部分违规**。SC-001 ~ SC-004 / SC-009 显式提到 Langfuse / Postgres / DashScope / LangGraph。这是有意的妥协（与 spec 001 / 002 一致——它们也用了这些名词），原因：spec 003 是 deep research agent 的实现 spec，技术栈已锁定（spec 001 FR-006 + constitution Constraint #8）；"technology-agnostic" 应理解为"不指定**未决定**的栈"。SC 的 metrics（≥ 8 sources / 0.5 秒阈值 / counter 范围）本身是 user-facing outcome，符合 spec template 的"Good examples"风格。
- [x] **CHK-009** All acceptance scenarios are defined — 实现了。5 个 US 共 13 个 Acceptance Scenarios，每条用 Given/When/Then 模板，含 Langfuse / DashScope 凭证就位条件 + 期望 state 字段 + Postgres 查询断言。
- [x] **CHK-010** Edge cases are identified — 实现了。8 条 Edge Cases（Bailian App 429 / App 凭证缺失 / ReRank SDKError / LLM Judge 越界 / websearch 空结果 / Send 单 worker 失败 / 跨轮 URL 重复 / counter +1 时机 / env var 缺失），每条给出具体处理路径（重试次数 / fallback / raise）。
- [x] **CHK-011** Scope is clearly bounded — 实现了。"Out of Scope" 章节明确列出 8 项不做（write 子图 / UI / Langfuse hooks / score 打分 / 动态 ReRank / 多模态 / Rate limit 抽象 / 内容长度排序），与 spec 002 / README §2 拆分策略一致。
- [x] **CHK-012** Dependencies and assumptions identified — 实现了。Dependencies 章节列 8 条（spec 001 / 002 / Postgres / Langfuse SDK / DashScope SDK / Bailian App / LangGraph 1.2.11+ / 3 篇 ADR）；Assumptions 章节列 9 条（含 Langfse/DashScope 可达性、手动创建 prompt、write 子图为 stub 预期等）。

## Feature Readiness

- [x] **CHK-013** All functional requirements have clear acceptance criteria — 实现了。FR-001 → SC-001 / SC-009；FR-002 → SC-001；FR-006 → SC-003 / SC-010；FR-009 → SC-001 / SC-004；FR-013 → SC-003 / SC-004；FR-014 → SC-004；FR-017 → SC-005 / SC-010。
- [x] **CHK-014** User scenarios cover primary flows — 实现了。US1（核心 P1：真实 web search 产出 Source）/ US2（P1：Send 并发验证）/ US3（P1：ReRank + LLM Judge）/ US4（P1：终止循环 + write 接力）/ US5（P2：USE_RERANK 降级）。
- [x] **CHK-015** Feature meets measurable outcomes defined in Success Criteria — 实现了。SC-001 是 US1 的端到端 smoke；SC-002 是 US2 的并发验证；SC-003 是 US3 的 ReRank 验证；SC-006 是 US4 的 write 接力验证；SC-005 是 US5 的降级验证。每个 SC 都关联到一个 US 的可观测 outcome。
- [x] **CHK-016** No implementation details leak into specification — **部分妥协**。spec 003 包含 `prompts.Application.call(applet_id=APP_ID, prompt=query, ...)` / `dashscope.TextReRank.call(model="gte-rerank-v2", ...)` / `Send("websearch", {...})` / `pg_advisory_xact_lock(hashtext(tid))` 等代码片段，集中在 FR-001 / FR-006 / FR-009 / FR-013。这是有意的（spec 001 / 002 也是如此），原因：这些是 SDK API 名称而非内部实现，**引用**它们是为了让 FR 行为可验证（"调 X API 拿到 Y 数据"），不指定**如何**调用（重试参数 / 异常处理 / 状态字段命名）的代码细节。读者能验证 FR 是否满足无需读代码。

## Notes

- spec 003 是 deep research agent 第二个真正"做实事"的 spec（spec 002 跑通 plan + interrupt，spec 003 跑通 research）。spec 003 后 Lesson 0007 可以做 `USE_RERANK=true/false` 对照实验。
- 最大的非默认决策是 [Q1]：用 `dashscope.Application.call` 而非 `Generation.call(enable_search=True)`——这是 [[dashscope-mmaas-enable-search-needs-bailian-app]] 项目记忆已确认的 footgun，spec 003 必须前置到 Clarifications 而不是让它在 plan 阶段被发现。
- CHK-008 与 CHK-016 的"technology-agnostic"妥协是 spec 001 / 002 已建立的一致性——deep research agent 是 implementation spec，不是抽象 product spec。constitution PRINCIPLE V "Simplicity" 鼓励"锁定已决定技术栈"，spec template 的"technology-agnostic"是指导原则不是 hard rule。
- 下一步：进入 `/speckit-plan` 阶段，输出 `plan.md` + `data-model.md` + `contracts/README.md` + `quickstart.md` + `tasks.md`（per spec 002 目录结构）。