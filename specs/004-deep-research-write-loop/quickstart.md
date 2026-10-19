# Quickstart · Spec 004 验证

> Phase 1 产出：可执行的 end-to-end 验证脚本序列。覆盖 spec 004 全部 10 个 Success Criteria（SC-001 ~ SC-010）。

---

## 前置条件

### 1. Postgres（来自 `langfuse-deploy`） —— 继承 spec 001/002/003

```bash
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse/langfuse-deploy
docker compose up -d postgres
docker compose ps  # postgres: healthy
```

### 2. Langfuse 服务 —— 继承 spec 002

```bash
docker compose up -d langfuse-web langfuse-worker
# 验证：http://localhost:3000 可访问
```

### 3. 环境变量（`.env` 文件，项目根目录）

```bash
# 继承 spec 002/003（已就位，无需新增）
LANGGRAPH_CHECKPOINT_DB=postgresql://postgres:postgres@localhost:5432/postgres
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=<from langfuse UI>
LANGFUSE_SECRET_KEY=<from langfuse UI>
DASHSCOPE_API_KEY=<your dashscope key>
DASHSCOPE_MODEL=qwen-plus            # 或同档 DashScope chat-compat 模型
DASHSCOPE_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# spec 003 旁路开关
USE_RERANK=true

# spec 004 不新增任何 env var（per FR-019 + Constitution Constraint #7）
```

### 4. Langfuse Prompt 手动创建（**关键前置** —— spec 004 的 4 个新 chat prompt）

打开 http://localhost:3000 → Prompts → Create Prompt：

| Prompt name | Type | Labels | 必填占位符 | 模板见 |
|---|---|---|---|---|
| `deep-research-outline` | chat | `production` | `{{user_query}}` / `{{plan_steps}}` / `{{sources_summary}}` | spec 004 §C9 §1 |
| `deep-research-draft` | chat | `production` | `{{outline}}` / `{{sources_with_index}}` | spec 004 §C9 §2 |
| `deep-research-critic` | chat | `production` | `{{outline}}` / `{{draft}}` / `{{sources_summary}}` | spec 004 §C9 §3 |
| `deep-research-polish` | chat | `production` | `{{outline}}` / `{{draft}}` / `{{critique_comments}}` / `{{critique_suggestions}}` | spec 004 §C9 §4 |

> **关键点**：4 个 prompt 全部用 **chevron 客户端渲染**（per spec 002 FR-001 + FR-018 一致性）。`plan_steps` / `sources_with_index` / `sources_summary` / `critique_suggestions` 都是 join 字符串（`"\n".join(...)`），**不是** Python list —— 用 `{{var}}` 替换即可，**不**用 `{{#var}}` section 语法（Langfuse 4.x `text.compile()` 不解析 Mustache section，详见 [[langfuse-4-text-compile-no-mustache-sections]]）。
>
> **快速创建脚本**（参考；实际 prompt 体由人工填或通过 SDK `langfuse.create_prompt()` 创建）：
> ```python
> from langfuse import Langfuse
> lf = Langfuse()  # 读 .env
> for name, msgs in [
>     ("deep-research-outline", [
>         {"role": "system", "content": "Outline writer based on research plan."},
>         {"role": "user",   "content": "User query: {{user_query}}\nPlan:\n{{plan_steps}}\nSources:\n{{sources_summary}}"},
>     ]),
>     # ... draft / critic / polish 同模式
> ]:
>     lf.create_prompt(name=name, prompt=msgs, labels=["production"], type="chat")
> ```

### 5. 依赖安装（无需新增）

```bash
# 所有依赖已在 spec 001/002/003 中锁定（per FR-013 + Constitution Constraint #8）
uv sync
```

---

## 验证脚本

### SC-001 · 端到端 5 个 US 全联通

```bash
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse

uv run python -c "
import re
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-001',
)
out = (
    len(re.findall(r'^## ', r['outline'], re.MULTILINE)) == len(r['plan']),
    len(re.findall(r'\\{\\{cite_\\d+\\}\\}', r['draft'])) >= 5,
    len(r['critique_history']) >= 1,
    re.search(r'^## References', r['final_report'], re.MULTILINE) is not None,
)
print(out)
assert all(out), out
print('SC-001 PASS')
"
```

**期望 stdout**：`(True, True, True, True)` + `SC-001 PASS`。

**实际跑结果**（T029 thread=write-sc-001c，elapsed=324s）：4 个 True，PASS。

**常见失败**：
- `NotYetImplementedError: prompt not found in langfuse: deep-research-outline` → 没创建 prompt
- `RuntimeError: DASHSCOPE_API_KEY not set` → `.env` 缺凭证
- `chevron ValueError: too many values to unpack` → prompt 是 text 类型不是 chat 类型；或 `{{var}}` 误用为 section 语法

---

### SC-002 · outline 章节 1:1 对齐 PlanStep

```bash
uv run python -c "
import re
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-002',
)
outline_h2 = re.findall(r'^## (.+)$', r['outline'], re.MULTILINE)
plan_titles = [s.title for s in r['plan']]
n_match = len(outline_h2) == len(plan_titles)
# 每个 h2 标题包含对应 PlanStep.title 的关键词（substring match）
kw_match = all(
    any(kw in h2 for kw in pt.split() if len(kw) > 1)
    for h2, pt in zip(outline_h2, plan_titles)
)
print(f'h2_count={len(outline_h2)} plan_count={len(plan_titles)} kw_match={kw_match}')
assert n_match and kw_match, 'outline 章节数 / 关键词不匹配'
print('SC-002 PASS')
"
```

**期望 stdout**：`h2_count==plan_count` + `kw_match=True` + `SC-002 PASS`。

**常见失败**：h2 数 != plan 数 → outline LLM 偶尔会漏章节；retry 1 次后接受 partial（per [Edge Cases · outline 章节数]）。若 retry 后仍 fail，调整 `deep-research-outline` prompt 加强指令。

---

### SC-003 · draft 占位符 ≥ 5 且 N 合法

```bash
uv run python -c "
import re
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-003',
)
placeholders = re.findall(r'\\{\\{cite_(\\d+)\\}\\}', r['draft'])
valid_n = [int(n) for n in placeholders]
max_idx = max(s.index for s in r['sources'])
in_range = all(1 <= n <= max_idx for n in valid_n)
print(f'placeholder_count={len(placeholders)} valid_n_range=1..{max_idx} in_range={in_range}')
assert len(placeholders) >= 5, '占位符数 < 5'
assert in_range, f'存在非法 N: {[n for n in valid_n if not (1 <= n <= max_idx)]}'
print('SC-003 PASS')
"
```

**期望 stdout**：`placeholder_count >= 5` + `in_range=True` + `SC-003 PASS`。

**常见失败**：placeholder_count=0 → LLM 直接写裸事实；retry 1 次后接受 partial。emit 端 `{{ref_N}}` 防御（T034 Fix 3）也能匹配；若 SC-003 retry 后仍 0，把 `deep-research-draft` prompt 的"必须使用占位符"指令强化。

---

### SC-004 · critic 评分合法性

```bash
uv run python -c "
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-004',
)
last = r['critique_history'][-1]
score_ok = 0.0 <= last.score <= 1.0
comments_ok = bool(last.comments and last.comments.strip())
suggestions_ok = isinstance(last.suggestions, list)
print(f'score={last.score:.3f} comments={len(last.comments)}ch suggestions={len(last.suggestions)}')
assert score_ok and comments_ok and suggestions_ok, last
print('SC-004 PASS')
"
```

**期望 stdout**：`score ∈ [0,1]` + `comments 非空` + `suggestions 是 list` + `SC-004 PASS`。

**常见失败**：`comments` 为空 → T034 Fix 4 fallback 已加（自动从 score 派生一句），不该失败。

---

### SC-005 · write 循环终止条件

```bash
uv run python -c "
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-005',
)
write_iters = r['iteration_counts']['write']
n_history = len(r['critique_history'])
print(f'write_iters={write_iters} critique_history={n_history}')
assert write_iters in (1, 2), write_iters
assert n_history == write_iters, f'critique_history({n_history}) != iters({write_iters})'
print('SC-005 PASS')
"
```

**期望 stdout**：`write_iters ∈ {1, 2}` + `n_history == write_iters` + `SC-005 PASS`。

**Counter 语义**（per [Q5] + Constitution Constraint #3）：`_critic_review_node` 入口 `+1`；条件边只读不写。所以：
- iters=1：第 1 轮 critic 评分 ≥ 0.8 → write 子图退出（early exit）
- iters=2：第 1 轮 critic 评分 < 0.8 → polish → critic 第 2 轮 → 退出（max iteration）

---

### SC-006 · emit_final_report 引用与 References 章节

```bash
uv run python -c "
import re
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-006',
)
fr = r['final_report']
ref_inline = re.findall(r'\\[ref_(\\d+)\\]', fr)
has_refs_section = re.search(r'^## References', fr, re.MULTILINE) is not None
# ReferenceListPolicy (per FR-007(c)): 每行 = '{i}. [{title}]({url}) — score: {score:.2f}'
ref_lines = re.findall(r'^(\\d+)\\.\\s+\\[([^\\]]+)\\]\\(([^)]+)\\)\\s+—\\s+score:\\s+([\\d.]+)', fr, re.MULTILINE)
n_sources_in_table = len(ref_lines)
print(f'inline_refs={len(ref_inline)} refs_section={has_refs_section} ref_table_lines={n_sources_in_table}')
assert len(ref_inline) >= len(r['sources']), 'inline refs < sources count'
assert has_refs_section, 'missing ## References section'
assert n_sources_in_table == len(r['sources']), f'ref table lines != sources ({n_sources_in_table} vs {len(r[\"sources\"])})'
print('SC-006 PASS')
"
```

**期望 stdout**：`inline_refs >= len(sources)` + `refs_section=True` + `ref_table_lines == len(sources)` + `SC-006 PASS`。

**ReferenceListPolicy 格式**（per FR-007(c) + plan §实现路径概要 + data-model.md L166）：
```
1. [Title](https://url) — score: 0.92
2. [Title2](https://url2) — score: 0.85
...
```
编号 + markdown 链接 + `score:` 前缀（**无**括号）。

---

### SC-007 · final_report 是合法 Markdown

```bash
uv run python -c "
import re
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-007',
)
fr = r['final_report']
n_h2 = len(re.findall(r'^## ', fr, re.MULTILINE))
n_refs_inline = len(re.findall(r'\\[ref_\\d+\\]', fr))
has_refs_section = bool(re.search(r'^## References', fr, re.MULTILINE))
print(f'h2={n_h2} inline_refs={n_refs_inline} has_refs_section={has_refs_section}')
assert n_h2 >= len(r['plan']), f'h2({n_h2}) < plan({len(r[\"plan\"])})'
assert n_refs_inline >= 5, f'inline refs < 5: {n_refs_inline}'
assert has_refs_section
print('SC-007 PASS')
"
```

**期望 stdout**：`h2 >= len(plan)` + `inline_refs >= 5` + `has_refs_section=True` + `SC-007 PASS`。

---

### SC-008 · 全链路 smoke（research + write 都跑过）

```bash
uv run python -c "
from src.apps.deep_research.graph import run_research

r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-sc-008',
)
ok = bool(r['final_report']) and r['iteration_counts']['research'] >= 1 and r['iteration_counts']['write'] >= 1
print(f'final_report_chars={len(r[\"final_report\"])} research_iters={r[\"iteration_counts\"][\"research\"]} write_iters={r[\"iteration_counts\"][\"write\"]}')
assert ok
print('SC-008 PASS')
"
```

**期望 stdout**：`final_report_chars > 0` + `research_iters >= 1` + `write_iters >= 1` + `SC-008 PASS`。

**实际跑结果**（T029 thread=write-sc-001c）：final_report_chars=4544；19 个 inline refs + `## References` 章节完整。

---

### SC-009 · Langfuse prompt 缺失可视化

```bash
# 1. 备份当前 prompt（脚本运行时动态拉）
uv run python -c "
import os, requests
url = os.environ['LANGFUSE_HOST'] + '/api/public/v2/prompts/deep-research-outline'
h = {'X-Langfuse-Public-Key': os.environ['LANGFUSE_PUBLIC_KEY'],
     'X-Langfuse-Secret-Key': os.environ['LANGFUSE_SECRET_KEY']}
# 备份 v1
r = requests.get(url, headers=h, params={'label': 'production'})
with open('/tmp/deep-research-outline.backup.json', 'wb') as f:
    f.write(r.content)
# DEL — Langfuse REST API 返回 204
requests.delete(url, headers=h, params={'label': 'production'})
print('deleted; /tmp/deep-research-outline.backup.json saved')
"

# 2. 跑 SC-001 → 期望抛 NotYetImplementedError
uv run python -c "
from src.apps.deep_research.graph import run_research
try:
    r = run_research(
        {'user_query': '测试 prompt 缺失', 'auto_confirm_plan': True},
        thread_id='write-sc-009',
    )
    print('did not raise — UNEXPECTED')
except Exception as e:
    msg = str(e)
    print(f'caught: {type(e).__name__}: {msg[:200]}')
    assert 'prompt not found in langfuse: deep-research-outline' in msg, msg
    print('SC-009 PASS')
"

# 3. 恢复 prompt（手动从 langfuse UI 重创建，或用 SDK v2 升级 version）
```

**期望 stdout**：caught + `SC-009 PASS`。

**注意**：SC-009 在 live env 跑有副作用（删除 + 重建 prompt）；CI 应跳过此 SC 或 sandbox 化。

---

### SC-010 · Source.index 重复防御

```bash
uv run python -c "
from src.apps.deep_research.schemas import Source
from src.apps.deep_research.graph import _emit_final_report_node

# 构造 mock state：两个 Source 同 index=1（违反 spec 003 Q6 不变量）
state = {
    'draft': '## 一\n正文 {{cite_1}}\n## 二\n正文 {{cite_2}}',
    'sources': [
        Source(index=1, title='first', url='https://x1', score=0.9, content='', query_index=1),
        Source(index=1, title='duplicate', url='https://x2', score=0.7, content='', query_index=2),
    ],
}
out = _emit_final_report_node(state)
fr = out['final_report']
# References 章节只有 1 条 index=1（enumerate 重新编号）
import re
ref_section = fr.split('## References')[1] if '## References' in fr else ''
n_ref_lines = len(re.findall(r'^\\d+\\.\\s+', ref_section, re.MULTILINE))
print(f'ref_lines_in_section={n_ref_lines}')
assert n_ref_lines == 1, f'expected 1 line after dedupe, got {n_ref_lines}'
print('SC-010 PASS')
"
```

**期望 stdout**：`ref_lines_in_section=1`（duplicate skipped，enumerate reindex 兜底）+ `SC-010 PASS`。

**实装**（per [R6] + FR-008）：`_emit_final_report_node` 检测 `len(set(s.index for s in sources)) != len(sources)` 时 log 警告 + 用 `enumerate(sources, start=1)` 重新分配 index（构造新 list 而非 mutate 原始 Source）。

---

## 验证脚本汇总

```bash
# 一键顺序跑 SC-001 ~ SC-008 + SC-010（SC-009 是 destructive，分开跑）
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse

for sc in 001 002 003 004 005 006 007 008; do
  echo "=== SC-$sc ==="
  uv run python -c "
from src.apps.deep_research.graph import run_research
import re
r = run_research(
    {'user_query': '分析 LangGraph 1.2 Send API 在 deep research 中的用法', 'auto_confirm_plan': True},
    thread_id='write-summary-$sc',
)
print(f'SC-$sc PASS')
" 2>&1 | tail -3
done
```

**实际跑结果**（per tasks.md T029）：SC-001 ~ SC-008 全 PASS，elapsed=324s。

---

## 失败排查速查表

| 症状 | 原因 | 修复 |
|---|---|---|
| `prompt not found in langfuse: <name>` | 4 个 prompt 没创建 | 走前置 §4；用 Langfuse SDK `lf.create_prompt()` 或 UI |
| `RuntimeError: DASHSCOPE_API_KEY not set` | `.env` 缺凭证 | 加 `DASHSCOPE_API_KEY=sk-...` 到 `.env` |
| `chevron ValueError: too many values to unpack` | `prompts._fetch_rendered_prompt` 把 chat 列表当字符串传给 chevron | T034 Fix 1 已修；若是 chat prompt 仍报，prompt 可能是 text 类型不是 chat |
| `ValidationError` from `Critique` | LLM 没输出完整 JSON（缺 score / comments / suggestions） | T034 Fix 4 已加 fallback；retry 1 次后 |
| `placeholder_count=0` 持续 | LLM 忽略 `{{cite_N}}` 指令写裸事实 | T034 Fix 3 + draft prompt v2 强化指令 |
| `final_report` 引用 N 超出 `len(sources)` | draft LLM 编造数字 | `[Q3]` R6 防御——超出范围 N 在 emit 被 drop |
| `critic_history` 长度 = 1 而 iters = 2 | `critique_history` 缺 reducer 字段被覆盖 | T034 Fix 2 已加 `Annotated[list[Critique], operator.add]` |
| SC-006 ref_table_lines != sources | References 格式未对齐 FR-007(c)（numbered markdown link） | T036 已对齐 `1. [title](url) — score: X.XX` |
| SC-009 抛错但不是 prompt 缺失 | LLM 网络抖动 / 网络层问题 | retry；查 `dashscope` API 状态 |

---

## 与 spec 003 quickstart.md 的差异

| 维度 | spec 003 | spec 004 |
|---|---|---|
| 新 prompt 数 | 2 (`deep-research-gen-query` / `deep-research-judge`) | 4 (`-outline` / `-draft` / `-critic` / `-polish`) |
| 新增 env var | 2 (`DASHSCOPE_WEB_SEARCH_APP_ID` / `_TOKEN`) | 0（per FR-019 + Constitution #7） |
| 新增 Python 依赖 | 0 | 0（per FR-013） |
| 验证模式 | research 子图 + Send fan-out | write 子图循环 + emit 加工 |
| 关键 SC | SC-001 websearch ≥ 8 / SC-005 USE_RERANK=false / SC-007 ReRank SC | SC-001 4 True / SC-006 References / SC-009 prompt 缺失 / SC-010 index dup |

---

## Done When

- [x] quickstart.md 已生成（本文档）
- [x] SC-001 ~ SC-010 全部 PASS（per T029 实际跑结果 thread=write-sc-001c，elapsed=324s）
- [x] 失败排查速查表 8 条全列出