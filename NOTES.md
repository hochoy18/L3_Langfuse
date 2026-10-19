# NOTES.md — 教学偏好与工作笔记

## 用户偏好（锁定）
- 语言：中文，口语化，避免空话
- 重视觉化：能用表格就不用段落，能用流程图描述就不用纯文字
- 代码示例必须能直接复制运行（python 3.11+、langfuse v3+）
- 每章末尾 1-2 个动手小任务
- 遇到坑用 "⚠️ 坑：" 开头明确点出
- 教学读者是自己（已锁定 OpenAI + LangChain/LangGraph）
- 2 天密集冲刺，可拆为 6-9 个 lesson

## 当前进度
- 2026-09-15：创建 workspace、敲定 mission、开始 lesson 1（部署）
- 已搜集资源：Langfuse v3 OpenAI 集成、Docker Compose 自托管、LLM-as-Judge、Prompt 管理
- 2026-09-16：lesson 2（hello trace）跑通；hello_trace 踩坑归档为 learning-record 0006
- 2026-09-16：lesson 3（LangGraph ReAct + CallbackHandler）完成，对应 demo 在 `src/apps/fuse/react_agent.py`
- 2026-09-16：lesson 4（Prompt Management）完成，demo 在 `src/apps/fuse/prompt_demo.py`，踩坑归档为 learning-record 0007
- 2026-09-16：lesson 5 评分系列完成（拆3 个子节）+ 5 个 prompt-* reference 文档沉淀 + scoring_demo / user_feedback_demo 跑通，踩坑归档为 learning-record 0008

## 待办
- ✅ lesson 1：本地部署
- ✅ lesson 2：最小接入 + hello trace
- ✅ lesson 3：LangGraph 集成
- ✅ lesson 4：Prompt 管理
- ✅ lesson 5：在线评估 + 用户反馈（拆 3 子节：0005-01 基础 / 0005-02 Evaluator / 0005-03 工作流）
- lesson 6：离线评估（dataset + experiment）
- lesson 7：评估结果分析与 dashboard
- lesson 8：踩坑复盘 + CI 集成思路

## 已沉淀的 reference（lesson 0004 配套）
- [[reference/prompt-data-model.html]]：Prompt 数据模型 —— Version / Label / 不可变性 / 默认行为 / 单一 vs 多 label
- [[reference/prompt-code-integration.html]]：代码接入 —— 4 个 SDK 调用 / @observe / 6 种 link 方式 / 绑定本质
- [[reference/prompt-operations-playbook.html]]：运维 SOP —— 6 个场景 / 5 步部署 / 缓存 / 回滚
- [[reference/prompt-storage-internals.html]]：存储内部 —— Postgres 表结构 / audit_logs / is_active 真相
- [[reference/prompt-pitfalls-cheatsheet.html]]：踩坑速查 —— 8 个常见坑 / 调试 SOP / 5 个反直觉认知

## 已沉淀的踩坑（按 lesson 倒序）
- lesson 5 → [[learning-records/0008-scoring-pitfalls.md]]：Score 写不进去的 5 个 root cause（name 没建 / categorical 越界 / 缺 trace_id / 跨 context / 忘 flush）/ `update_current_generation(usage=)` TypeError / Judge 输出不稳
- lesson 4 → [[learning-records/0007-prompt-management-three-pitfalls.md]]：三个 v3→v4 边界坑——`update_current_generation(prompt=...)` 必须传 PromptClient 对象而非字符串；`usage=` 不存在要 `usage_details=`；v4 events_only 模式让传统 `/api/public/traces` 404，要用 v2/observations
- lesson 3 → [[learning-records/0006-hello-trace-custom-openai-client.md]]：两个不显然问题——非标准 env 名的 OpenAI 客户端构造；`from langfuse.openai import openai` 的真实作用是 monkey-patch 的副作用 import
- lesson 1 → [[learning-records/0004-worker-redis-socket-timeout.md]]：v4 worker 报 redis socket timeout，根因是 clickhouse 抢 CPU