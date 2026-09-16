# Mission: Langfuse 实战教学 — 让自己的 Agent 项目具备可观测性与评估能力

## Why
我正在做 LLM Agent 项目（OpenAI + LangChain/LangGraph 栈），目前只能靠 log + 人工抽查定位问题、对比 prompt 效果。希望两天内掌握 Langfuse，把现有 Agent 接入完整 trace，并为后续上线后能持续做评估和质量监控打好地基。

## Success looks like
- 我能在本地用 Docker Compose 一键拉起 Langfuse，并理解每个组件（postgres / redis / minio）的角色
- 我能把自己的 LangGraph ReAct Agent 接入 Langfuse，UI 中能看到完整多步 trace（含工具调用、token、延迟）
- 我能配置一个 LLM-as-a-Judge 评估器 + 一个离线 dataset 实验，跑出两个 prompt 版本的对比结果
- 我能识别并讲解 Langfuse trace 中的常见异常模式（递归爆炸、token 滥用、prompt 漂移）

## Constraints
- **时间**：2 天密集冲刺，每天 4-6 小时
- **场景**：自学 + 落地到自己的 Agent 项目；示例必须能直接复用到我的代码里
- **栈**：Python 3.11+、OpenAI gpt-4o / 4o-mini、LangChain / LangGraph ReAct、Docker Desktop 已装
- **样式**：中文、口语化、表格优先、遇到坑要明确提示
- **预算**：优先自托管（不订阅 Langfuse Cloud），便于后续迁移到团队环境

## Out of scope
- Langfuse 企业版功能（SSO、审计日志、私有部署高级选项）
- Kubernetes / Helm 大规模部署
- OpenTelemetry 协议的深度自定义（先用 SDK 满足需求）
- 自建评估服务 / 远程评估（先做 in-process 评估器）
- 多语言 SDK（JS/TS 暂不覆盖；如有需要可作为后续 lesson）