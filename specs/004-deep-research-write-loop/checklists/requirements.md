# Specification Quality Checklist: Write 阶段（真实 outline / draft / critic / polish + emit_final_report）

**Purpose**: 验证 spec 004 在进入 `/speckit-plan` 之前的需求完整性与质量。
**Created**: 2026-10-19
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] **CHK-001** No implementation details（languages / frameworks / APIs）— 实现了。spec 004 提到 LangGraph / DashScope / Langfuse 是**已冻结的依赖**（spec 001/002/003 已锁定在 `pyproject.toml`），不是新引入；Send API / chevron 渲染 / `re.sub` 等"how"仅出现在 NFR 约束段落（与既有 spec 001/002/003 一致），核心 US/FR 用业务语言（"outline 章节与 PlanStep 1:1"、"draft 用占位符"、"emit_final_report 加工合法 Markdown"）。
- [x] **CHK-002** Focused on user value and business needs — 实现了。5 个 User Story 全部从 Editor / Critic / 系统出口 视角写"作为 ... 我能 ..."，每个 US 配独立测试 + 验收场景。
- [x] **CHK-003** Written for non-technical stakeholders — 大部分满足。Q1-Q7 Clarifications 是技术决策但写在显式章节，US/FR 部分业务读者可读。NFR-001（latency）可被业务读者理解为"写作阶段不能太慢"。
- [x] **CHK-004** All mandatory sections completed — 实现了。User Scenarios & Testing（5 US + 9 Edge Cases）/ Requirements（20 FR + 4 NFR）/ Success Criteria（10 SC）/ Assumptions / Out of Scope / Dependencies / Notes 全部齐全。

## Requirement Completeness

- [x] **CHK-005** No [NEEDS CLARIFICATION] markers remain — 实现了。spec 004 在 Clarifications 章节前置 7 个 Q&A（Q1 prompt 拆分 / Q2 LLM role 新增 / Q3 占位符防御 / Q4 Critique schema / Q5 counter +1 时机 / Q6 emit 拓扑 / Q7 Source.index 重编号），把架构层决策提前解决，**不**在 FR 段落留 [NEEDS CLARIFICATION] 让 plan 阶段拍板。
- [x] **CHK-006** Requirements are testable and unambiguous — 实现了。20 个 FR 全部是 MUST 行为描述 + 可执行命令（FR-001 outline 节点接口 / FR-002 draft 占位符 / FR-005 条件边判据 / FR-007 emit 加工 / FR-010 prompt 占位符表 / FR-014 Critique schema 升级）。
- [x] **CHK-007** Success criteria are measurable — 实现了。10 个 SC 全部含 `python -c "..."` 命令，每条可由 Lesson 0007 实验脚本机械化跑。SC-001 的 4 个 True、SC-002 的章节对齐、SC-003 的占位符数、SC-005 的 counter 范围、SC-006 的 References 章节、SC-010 的 Source.index 重复防御都是明确可量化。
- [x] **CHK-008** Success criteria are technology-agnostic — **部分违规**（与 spec 003 一致性妥协）。SC-001 ~ SC-008 显式提到 Langfuse / Postgres / DashScope / LangGraph。SC 的 metrics（≥ 5 placeholder / h2 章节数对齐 / counter 范围）本身是 user-facing outcome，符合 spec template 的"Good examples"风格。
- [x] **CHK-009** All acceptance scenarios are defined — 实现了。5 个 US 共 13 个 Acceptance Scenarios，每条用 Given/When/Then 模板，含 Langfuse / DashScope 凭证就位条件 + 期望 state 字段 + Postgres 查询断言。
- [x] **CHK-010** Edge cases are identified — 实现了。9 条 Edge Cases（outline 章节数不一致 / draft 占位符缺失 / critic score 越界 / JSON fence / polish 缩减过大 / DASHSCOPE_API_KEY 缺失 / counter +1 时机 / emit sources 空 / HttpUrl 无效），每条给出具体处理路径（重试次数 / fallback / log 警告）。
- [x] **CHK-011** Scope is clearly bounded — 实现了。"Out of Scope" 章节明确列出 9 项不做（Streamlit UI / Langfuse hooks / 多模型 ensemble / 多模态 / 实时预览 / 语言切换 / score 打分 / 引用优先级 / Reference 节选），与 spec 002/003 / README §2 拆分策略一致。
- [x] **CHK-012** Dependencies and assumptions identified — 实现了。Dependencies 章节列 10 条（spec 001/002/003 + langfuse-deploy Postgres + Langfuse SDK + DashScope SDK + LangGraph + 2 篇 ADR）；Assumptions 章节列 10 条（含 Langfuse/DashScope 可达性、手动创建 4 个 prompt、write 不调 ReRank、PolishedDraft 不独立存字段等）。

## Feature Readiness

- [x] **CHK-013** All functional requirements have clear acceptance criteria — 实现了。FR-001 → SC-001 / SC-002；FR-002 → SC-001 / SC-003；FR-003 → SC-004；FR-005 → SC-005；FR-007 → SC-006 / SC-007；FR-008 → SC-010；FR-009 → SC-007；FR-014 → SC-004。
- [x] **CHK-014** User scenarios cover primary flows — 实现了。US1（核心 P1：outline 章节对齐）/ US2（P1：draft 占位符填充）/ US3（P1：critic 评审打分）/ US4（P1：polish 循环）/ US5（P1：emit_final_report 加工）。
- [x] **CHK-015** Feature meets measurable outcomes defined in Success Criteria — 实现了。SC-001 是 5 US 联合的端到端 smoke；SC-002 是 US1 的章节对齐验证；SC-003 是 US2 的占位符验证；SC-004 是 US3 的 critic 评分验证；SC-005 是 US4 的循环终止验证；SC-006 / SC-007 是 US5 的 emit 验证；SC-008 是全链路 smoke；SC-009 / SC-010 是错误路径验证。
- [x] **CHK-016** No implementation details leak into specification — **部分妥协**（与 spec 003 一致性）。spec 004 包含 `prompts.get_prompt("deep-research-outline", label="production").compile(...)` / `re.sub(r'\{\{cite_(\d+)\}\}', lambda m: f'[ref_{m.group(1)}]', draft_text)` / `graph.add_node("emit_final_report", ...)` / `pg_advisory_xact_lock(hashtext(tid))` 等代码片段，集中在 FR-001 / FR-003 / FR-007 / FR-009。这是有意的（spec 001/002/003 也是如此），原因：这些是 SDK API 名称或确定性 transformation 表达式，**引用**它们是为了让 FR 行为可验证，不指定**如何**调用的代码细节。读者能验证 FR 是否满足无需读代码。

## Notes

- spec 004 是 deep research agent **最后一段"做实事"**的 spec。前 3 个 spec 准备基础设施（graph 骨架 / plan 审阅 / research 真实跑），spec 004 完成 write 后 agent 已经可以端到端产出 `final_report` Markdown。spec 005 / 006 是 UI 包装与可观测性增量。
- 最大的非默认决策是 [Q6]：把 `emit_final_report` 放到外层图而非 write 子图内——这是 ADR-0001 设计的"future-trigger"兑现（外层节点加 1 时考虑平铺）。write 子图保持纯循环结构，emit 是确定性外层节点。
- 第二个非默认决策是 [Q3]：让 draft 节点用 `{{cite_N}}` 占位符而非直接写 `[ref_N]`——这是引用质量的核心防御。LLM 经常在 `[ref_N]` 处编造数字（LLM 脑补 `[ref_42]` 而 source 列表只到 [ref_8]），占位符 + emit 替换让引用编号**永远**对应真实 Source.index。
- 第三个非默认决策是 [Q4]：把 Critique schema 从 `score + comments` 升级到 `score + comments + suggestions`——是 spec 001 schema 的 MINOR 升级，向后兼容（旧实例只有 score+comments，polish 节点读 `critique.suggestions` 为空时 fallback 到 `comments` 派生）。
- CHK-008 与 CHK-016 的"technology-agnostic"妥协是 spec 001 / 002 / 003 已建立的一致性——deep research agent 是 implementation spec，不是抽象 product spec。constitution PRINCIPLE V "Simplicity" 鼓励"锁定已决定技术栈"，spec template 的"technology-agnostic"是指导原则不是 hard rule。
- 下一步：进入 `/speckit-plan` 阶段，输出 `plan.md` + `data-model.md` + `contracts/README.md` + `quickstart.md` + `tasks.md`（per spec 002/003 目录结构）。