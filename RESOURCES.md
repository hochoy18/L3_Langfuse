# Langfuse Resources

## Knowledge

### 官方核心文档
- [Langfuse Docs — Home](https://langfuse.com/docs)
  官方文档入口。所有 lesson 的事实基础从这里出。
- [Langfuse Python SDK v3 — Observability](https://langfuse.com/docs/observability/sdk/python/decorators)
  Python SDK 的核心 API：`@observe` 装饰器、`get_client()`、`start_as_current_observation()` 上下文管理器、`propagate_attributes()`、`update_current_trace()`、`flush()`。lesson 2、3 都引用这里。
- [OpenAI SDK Integration (Python)](https://langfuse.com/docs/integrations/openai)
  `from langfuse.openai import openai` 的 auto-instrumentation 模式。lesson 2 的最小示例直接来自这里。
- [Prompt Management](https://langfuse.com/docs/prompt-management/get-started)
  `create_prompt()` / `get_prompt()` / `compile()` / `get_langchain_prompt()`。lesson 4 用。
- [LLM-as-a-Judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
  评估器模板、dataset、experiment 流程。lesson 6 用。

### 自托管
- [Langfuse Self-Hosting — Docker Compose](https://langfuse.com/self-hosting/docker-compose)
  本地一键部署的官方指南。lesson 1 用。
- [langfuse/docker-compose.yml (GitHub)](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml)
  Compose 文件源文件。要用最新版的必须看这里，文档可能落后。

### 框架集成
- [LangChain Integration](https://langfuse.com/docs/integrations/langchain)
  LangfuseCallbackHandler + LangChain 变量语法转换。
- [LangGraph Integration](https://langfuse.com/docs/integrations/langgraph)
  LangGraph 专用集成，ReAct Agent 多步 trace 关键。
- [Cookbook: OpenAI Integration](https://langfuse.com/docs/guides/cookbook/integration_openai_sdk)
  完整可运行 cookbook，含 token 计算、metadata、session 管理。

## Wisdom (Communities)

- [Langfuse GitHub Discussions](https://github.com/orgs/langfuse/discussions)
  官方社区，问题响应快，常见集成坑这里都有人踩过。
- [Langfuse Discord](https://langfuse.com/discord)
  实时问答频道，活跃度高，适合卡住时求助。
- [r/LocalLLaMA 子版（关联子版）](https://reddit.com/r/LocalLLaMA)
  虽然主要讨论本地模型，但 observability / eval 话题也常出现，可交叉参考。

## Gaps
- **LangGraph + ReAct + 工具调用的完整可运行示例**：官方文档分散在多页，没一个一站式 cookbook。需要在 lesson 3 中自己拼装。
- **CI/CD 集成做回归测试的工程实践**：官方文档有概念但缺少具体 GitHub Actions YAML 示例。lesson 8 中会自己写一个。
- **OpenTelemetry 协议在 Langfuse 中的细粒度自定义**：超出本次 mission 范围，留作后续学习。