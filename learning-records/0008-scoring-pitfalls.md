# Lesson 0005 踩坑汇总 · Score 写不进去 / 写错了 / 烧钱 / 找不到

把 Part 1-3 三节 lesson 中提到的所有坑一次性归档。给后面 lesson 引用 + 用户查表。

**Why:** Score 写入看起来简单，但 5 个字段 × 4 种 parent × 3 种 evaluator × 5 种 data_type 的组合下，新人 80% 的时间在排查"为啥 UI 不显示 / 显示了但不对"。集中归档节省排查时间。

**How to apply:**
- 写 score 前先查表确认你的用法是否命中某个坑
- lesson 0005-03 §9.1 的 5 个 root cause 决策树是首选排查工具

---

## 坑 1 · score 不出现在 UI 上（5 个 root cause）

按排查顺序列：

| # | 原因 | 现象 | 排查方法 |
|---|---|---|---|
| 1.1 | **name 没在 UI Score Configs 建过** | Score API 不报错（200 OK），但 UI 找不到这条 score | UI → Score Configs → 搜你的 name，没就补上 |
| 1.2 | **CATEGORICAL 的 value 不在 config 预定义里** | HTTP 400 / LangfuseError: invalid category | 看 config 的 categories 列表，把 value 加进去 |
| 1.3 | **挂 observation 时没传 trace_id** | SDK 报 "observation_id requires trace_id" 或 silent fail | create_score 时同时给 trace_id + observation_id |
| 1.4 | **跨 observation context 调 score_current_trace** | RuntimeError: no active trace | 不在 @observe / CallbackHandler 内部时改用 create_score |
| 1.5 | **flush() 之前已经退出进程** | 短脚本最常见；score 在内存 queue 里没发出去 | 短脚本必须显式 flush()，位置在所有 score 写入之后 |

**Why:** 5 个 root cause 覆盖了 95% 的"score 不出现"问题。lesson 0005-03 §9.1 的 SVG 决策树把这个流程固化。

---

## 坑 2 · `update_current_generation(usage=...)` 报 TypeError

直觉写 `usage={...}`，v3 SDK 参数名是 `usage_details`。签名是：
```python
def update_current_generation(
    *, name, input, output, metadata, version, level,
    status_message, completion_start_time, model, model_parameters,
    usage_details, cost_details, prompt
) -> None
```

**Why:** Langfuse v3 整体在往 OTel 协议对齐，参数命名跟着 OTEL 的 `usage_details` 走，不是自创的 `usage`。

**修法：**`usage_details={"input_tokens": ..., "output_tokens": ..., "total_tokens": ...}`

**Evidence:** lesson 0005-02 第一次跑 demo 时报错 "unexpected keyword argument 'usage'"。

---

## 坑 3 · Judge prompt 没强 JSON 约束，输出格式不稳

LLM 输出不保证 JSON——30% 概率会拿到 prose 包裹的 JSON，或纯 prose。

**修法（lesson 0005-02 §6.4 三 trick 全部用上）：**
1. **prompt 强约束**："Output ONLY a JSON object, no preamble"
2. **低 temperature**（0-0.3）让 judge 输出分布更窄
3. **OpenAI 的 `response_format={"type": "json_object"}`**（如 provider 支持）服务端强制

**Why:** 不强约束，30% 的 judge 输出会触发 parse fallback，score 数据里混进噪声。

**Evidence:** lesson 0005-02 scoring_demo 的 fallback 路径就是为这种情况预留的。

---

## 坑 4 · judge 用同一个模型评自己（同 self-evaluation）

业务 LLM 和 judge 用同一模型 → judge 输出受业务 LLM 的输出分布污染，循环自我确认。

**修法：**judge 用<strong>不同模型</strong>。业务 MiniMax-M3 → judge 用 GPT-4o-mini 或 Claude Haiku。

**Why:** 文档明确说"don't let the judge see its own output"，但 self-evaluation 的危害比"看自己输出"更深——同模型的输出分布本身就容易让 judge 偏向原模型的偏好。

---

## 坑 5 · LLM judge 烧钱（sampling rate 没设）

Rule filter 默认 sampling_rate=1.0 + filter 范围宽 → 100% trace 跑 judge → 月账单爆炸。

**修法：**
- sampling_rate=0.05-0.1（5-10%）
- filter 用 `isRootObservation=true`（每条 trace 只评 root，不评子 observation）
- 配合 CATEGORICAL score 而不是 NUMERIC（成本更低）

**Why:** LLM judge 单次 ~$0.001-0.01；production 上每条 trace 都跑会超预算。

**Evidence:** lesson 0005-02 §6.6 明确推荐 sampling 0.05-0.1 + narrow filter。

---

## 坑 6 · CATEGORICAL 漏写 Score Config → HTTP 400

直接 `lf.create_score(name="quality", value="good", data_type="CATEGORICAL", trace_id=...)` 会报 400。

**修法：**先在 UI Score Configs 建好 config，categories=["good", "neutral", "bad"]，代码里只引用。

**Why:** 服务端用 config 做"哪些 category 合法"的强校验。

---

## 坑 7 · v4 trace-level evaluator 弃用（2026-11-16 cutover）

旧代码 / 旧项目如果把 evaluator 挂在 trace 级：
- v4 migration 期内：能跑但有 warning
- 2026-11-16 后：强制切到 observation-level

**修法：**evaluator 挂 observation-level + filter `isRootObservation=true`（这是 v3+ 推荐标准）。

**Why:** trace-level 上下文加载整个 trace 的所有 observation，慢；observation-level 只看单个 observation 快。

**Evidence:** lesson 0005-01 §3.3 提到过，lesson 0005-02 §6.8 是专门 callout。

---

## 坑 8 · Judge prompt 塞进了 Prompt Management

直觉用 Prompt Management 管 judge prompt——<strong>不行</strong>。

**修法：**Judge prompt 放在 Evaluator 资源自己的 definition 字段里（"Updating its definition creates a new version, and active rules automatically use the latest version"）。

**Why:** 业务 prompt（chat-system / critic / movie-critic）和 judge prompt 是两个不同的资源，业务 prompt 是给 production 代码用的，judge prompt 是给 evaluator 用的。

**Evidence:** lesson 0005-02 §6.5 明确说"this is a common misconception"。

---

## 坑 9 · 多次 `score_current_trace` 调用只显示最后一条

直觉以为会"合并"——实际是覆盖式（如果 score_id 相同）或并列（如果不同）。默认 score_id 不指定时为新 score，但 UI 显示顺序可能跟调用顺序不一致。

**修法：**
- 多次打分用不同 name（如 `judge-helpfulness` + `judge-tone`），让每条 score 都是独立 entry
- 想覆盖时显式传 `score_id=...`（同 id 写入会替换）
- 大多数场景你应该用不同 name 而不是覆盖

**Why:** score 写库是 append 语义，不是 replace 语义；UI 按时间倒序显示。

---

## 坑 10 · User feedback 当作 ground truth 来优化 prompt

User 👍👎 数据有采样偏差（详见 lesson 0005-03 §7.7）：
- 主动点 👍 的是开心用户
- 主动点 👎 的不一定真差（可能用户心情不好）
- 沉默用户不反馈（多数）

**修法：**user feedback 当<strong>辅助信号</strong>，主评测仍是 LLM Judge + Code Evaluator + Annotation Queue。User feedback 用来发现"judge 漏判的痛点"（如 👎 多 + judge 分数高 → 投到 Annotation Queue 让专家精标）。

**Why:** 采样偏差会让"追求 👍 多"等同于"优化讨好"，不是"优化有用"。

---

## 排查 SOP（按顺序走）

1. **控制台看异常**——Score API 抛 ValidationError / LangfuseError 直接看堆栈
2. **UI Score Configs**——确认 name + categories
3. **4 个 parent 字段**——挂 observation 必须给 trace_id + observation_id
4. **context 检查**——score_current_* 必须在 observation 内
5. **flush 时机**——短脚本必须显式 flush()
6. **trace_id 真实存在**——拼错或 trace_id 已 flush 都会被 silently 丢弃
7. **prompt 关联**——传的是 PromptClient 对象不是字符串

## 跨 lesson 引用

| Lesson | 哪些坑详细讲过 |
|---|---|
| 0005-01 §1.3 FAQ | 坑 5（sampling rate）的背景 / 坑 7（v4 deprecation）|
| 0005-02 §6.4-§6.7 | 坑 3（judge 输出）/ 坑 4（self-eval）/ 坑 8（judge prompt 位置）|
| 0005-03 §7.7 + §9 | 坑 10（user feedback 当 ground truth）+ 全部坑排查表 |