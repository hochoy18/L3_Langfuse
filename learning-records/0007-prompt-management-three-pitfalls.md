# Lesson 0004 的三件事：prompt 类型、参数名、API 版本

跑 prompt_demo 时踩了三个非显而易见的坑，都和"Langfuse v3 SDK 在 v4 server 上跑"的边界条件有关。记下来给后面 lesson 引用。

## 1. `update_current_generation(prompt=...)` 必须传 PromptClient 对象，不是字符串

官方文档示例写的是 `langfuse.update_current_generation(prompt=prompt)` 其中 `prompt` 是 `get_prompt()` 返回的对象。

**坑点：**
- 如果你传 compile 后的字符串（`compiled`），UI 上 input 字段会有 prompt 文字，**但不会建立 Prompt 徽章链接**
- 必须传 `p = lf.get_prompt(...)` 这个对象本身
- 验证方式：调完 LLM 后用 `GET /api/public/v2/observations?fields=...prompt` 看是否有 `promptName` / `promptVersion` 字段

**Why:** Langfuse 需要从 PromptClient 对象读 `.name` / `.version` / `.labels` 这些属性才能建立版本链接。字符串没有这些信息，所以只能落到 input 字段。

**How to apply:**
- 任何"我要让这条 trace 上的 generation 链接到具体 prompt 版本"的场景，都传 PromptClient 对象
- lesson 0004 的 HTML / demo 都已修正，并加了显式 callout

**Evidence:** 第一次跑 demo 后用 v2 API 查 observation，没有 `promptName` 字段（虽然 trace 跑通）；改成 `prompt=p` 后立刻能看到 `promptName: "movie-critic"`, `promptVersion: 1`。

## 2. `update_current_generation(usage=...)` 不存在——参数名叫 `usage_details`

直觉写 `usage={...}`，但 SDK v3 的参数名是 `usage_details`（带下划线的全名）。签名是：

```python
def update_current_generation(
    *, name, input, output, metadata, version, level,
    status_message, completion_start_time, model, model_parameters,
    usage_details, cost_details, prompt
) -> None
```

**Why:** Langfuse v3 整体在往 OTEL 协议对齐，参数命名也跟着 OTEL 的 `usage_details` 走，不是自创的 `usage`。

**How to apply:**
- 任何 `update_current_*` 系列方法，先 `inspect.signature(...)` 看实际参数名
- lesson 0004 demo / HTML 已修正为 `usage_details={"input_tokens": ..., "output_tokens": ..., "total_tokens": ...}`

**Evidence:** 第一次跑 demo 时直接 `TypeError: Langfuse.update_current_generation() got an unexpected keyword argument 'usage'`，trace 在 UI 上记成 `level=ERROR`。

## 3. Langfuse v4 server 默认是 `events_only` 模式，传统 `/api/public/traces` 接口会 404

本地部署的 Langfuse v4 在默认配置下走 `events_only` 写入模式，传统 batch ingestion API（`/api/public/ingestion`、`/api/public/traces` 等）直接 404。

**坑点：**
- 想用 `curl /api/public/traces` 验证 trace 是否上传会被 404 + 误导信息（"endpoint not available on events_only mode"）
- 需要改用 v2 的 observations API：
  ```
  GET /api/public/v2/observations?fields=core,basic,usage,prompt
  ```
- 或者用 OTEL 端点 `/api/public/otel/v1/traces`（SDK 自动走这条）

**Why:** Langfuse v4 把 trace ingestion 完全迁到 OTEL 协议，旧的 Langfuse 自有 batch API 是 deprecated。在自托管 v4 默认 events_only 模式下，旧 API 被服务端硬性拒绝。

**How to apply:**
- lesson 0004 之后的所有 lesson，如果想用 API 验证 trace，<strong>用 v2/observations，不要用 /traces</strong>
- 验证 prompt 关联的字段：`promptName` / `promptVersion` / `promptId` 三个字段都在 → 关联成功
- 验证 token 上报：字段是 `usageDetails`（注意 s 在 Details），keys 被 normalize 成 `input`/`output`/`total`（即便 SDK 收到的是 `input_tokens`/`output_tokens`/`total_tokens`）

**Evidence:** 自托管 v4 的 docker-compose 启动后 `curl /api/public/traces` 返回 `{"message": "This endpoint is not available on deployments running in Langfuse v4 events_only mode"}`；改用 v2/observations 正常返回。

## 4. 三个坑的共同根因

都是"**文档示例停留在 v3 时代**"导致。具体来说：
- `prompt=prompt_obj` 这种关键 API 在文档里示例代码截断了一行，容易被误读
- `usage=` 这种直觉命名被 OTEL 的 `usage_details` 覆盖
- 自托管 v4 默认开了 events_only，但 SDK 文档没强调配套 API 也跟着变了

排查 SOP（给后续 lesson 用）：
1. **API 名错了**：`inspect.signature(client.method)` 看实际参数
2. **API 调用 404 / 410**：检查 Langfuse 版本和 `events_only` 模式
3. **prompt 链接没建**：检查 `update_current_generation(prompt=...)` 传的是 PromptClient 对象，不是字符串