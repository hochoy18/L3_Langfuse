# Quickstart: Deep Research Streamlit UI (spec 005)

**Purpose**: spec 005 的 9 个 Success Criteria（SC-001 ~ SC-009）的可运行验证步骤。每条步骤给出：先决条件 + 跑什么 + 期望结果（PASS/FAIL 信号）。

**Created**: 2026-10-19

---

## 先决条件（跑 quickstart 前）

```bash
# 1. 已安装项目 + spec 005 新依赖
cd /Users/cai.he/Desktop/AI/202607_智泊/Learning/L3_Langfuse
pip install -e ".[dev]"
# streamlit>=1.30.0 + pytest>=8.0 + pytest-cov>=5.0 + pytest-asyncio>=0.23

# 2. Postgres + Langfuse env 已就位
psql $LANGGRAPH_CHECKPOINT_DB -c "SELECT 1;"
# 期望：返回 1 行 1

# 3. spec 001-004 已运行过至少一次（checkpoints 表已存在）
python setup.py  # 或项目内 setup 脚本（per spec 001 SC-004）

# 4. 端口 8501 未占用（Streamlit 默认端口）
lsof -i :8501
# 期望：空输出
```

---

## SC-001: Streamlit 单页 plan review / Approve / Submit Edit 端到端

**先决条件**：先决条件 §1-§4 + 浏览器打开 `http://localhost:8501`

**步骤**：

```bash
# Terminal 1: 启动 Streamlit
streamlit run src/apps/deep_research/app.py
# 期望：参数打印 "You can now view your Streamlit app in your browser." + URL
```

```text
# 浏览器:
# 1. 在 "研究问题" 文本框输入: "为什么 LangGraph 1.2 用 interrupt?"
# 2. 点 "开始研究" 按钮
# 3. 期望: 出现 "研究进行中..." spinner；30-120s 后页面切到 Plan 审阅
# 4. 期望: Plan 编号 (1, 2, 3) + 标题 + 描述以 ### / Markdown 渲染
# 5. 点 "Approve" 按钮
# 6. 期望: spinner 重新出现；30s-6min 之后页面切到 Final Report 视图
# 7. 期望: st.markdown 渲染报告 + "下载 Markdown" button 可见
```

**PASS 信号**：
- Plan 审阅页出现 `### 1. {title}` 等标题（至少 1 个 step）
- Approve 后 spinner 重新出现 30s+（在跑 research + write subgraph）
- 最终报告页 Markdown 长度 > 1000 字（含 `## References` 节 + `[ref_N]` 引用）
- "下载 Markdown" button 能下载 `<thread_id>.md`

**FAIL 信号**：
- Plan 页空白 / Plan 不渲染 → 检查 `format_plan_md` + `__interrupt__` 检测
- Approve 后卡死 spinner 永远不结束 → 检查 `Command(resume="approve")` 接线
- 最终报告空字符串 → 检查 `emit_final_report_node` + `state["final_report"]`

---

## SC-002: Streamlit 跨 tab resume

**先决条件**：SC-001 跑通 + 记下 thread_id（如 `sidebar` 可见的 `<uuid>`）

**步骤**：

```text
# Browser tab 1: streamlit run + localhost:8501 + 跑通 SC-001，记下 thread_id=T1
# Browser tab 2: 打开同 URL localhost:8501
#   1. 在 sidebar 看到 thread_id 文本框（默认是新 uuid，disabled）
#   2. 在 "Load thread" 文本框输入 T1
#   3. 点 "Load thread" 按钮
#   4. 期望: sidebar 显示 T1 + 主区显示之前 SC-001 的 Final Report
#   5. 点 "Reset thread" 按钮
#   6. 期望: thread_id 切回新 uuid
```

**PASS 信号**：
- Tab 2 能 read T1 的 final_report 内容（与 Tab 1 一致）
- "Load thread" / "Reset thread" 切换 thread_id 后 UI 立即刷新
- 切回新 uuid 后 "研究问题" 文本框为空（session_state 干净）

**FAIL 信号**：
- Tab 2 显示空白 / final_report 不出现 → 检查 sidebar thread_id 接线 + Postgres `checkpoints` 表读取
- 切 thread 后残留旧 final_report → 检查 session_state 隔离

---

## SC-003: Streamlit spinner UX（per Q4 澄清，2026-10-19）

**先决条件**：先决条件 §1-§4

**步骤**：

```text
# Browser tab 1:
#   1. 输入 query → 点 "开始研究"
#   2. 期望: 立刻出现 "研究进行中..." spinner（不切换 Plan / Research / Write 阶段）
#   3. 等 30s-6min
#   4. 期望: spinner 区切到 "✅ 完成"
#   5. 期望: 不出现 "Plan 生成中..." → "Research 中..." → "Write 中..." → "完成" 的 4 阶段切
```

**PASS 信号**：
- Spinner 区文案始终是 "研究进行中..."（不变）直到切到 "✅ 完成"
- **不**出现 4 阶段 placeholder 切换（per FR-010 + Q4 澄清）

**FAIL 信号**：
- Spinner 区文案多次切换（如 "Plan → Research → Write"）→ spec 005 违反 FR-010 Q4 决策
- Spinner 区卡死 → 检查 `_run_research_with_stages` 单 spinner 包裹

---

## SC-004: CLI smoke test 端到端

**先决条件**：先决条件 §1-§3

**步骤**：

```bash
# Terminal 1: 跑 CLI smoke
python -m src.apps.deep_research.cli "为什么 LangGraph 1.2 用 interrupt?"
# 期望 stderr:
#   [cli] thread_id=<uuid>
#   [cli] query=为什么 LangGraph 1.2 用 interrupt?
# 期望 stdout: <final_report markdown>
echo $?
# 期望: 0
```

**PASS 信号**：
- stderr 出现 `[cli] thread_id=<uuid>` + `[cli] query=...`
- stdout 是非空 Markdown（含 `## References` 节）
- `echo $?` = 0

**FAIL 信号**：
- stderr 出现 `[ERROR] ...` → 检查 `run_research` 调用
- stdout 是 `{"error": ...}` 而非 Markdown → CLI 没正确解析 result
- `echo $?` != 0 → exit code 错

---

## SC-005: CLI exit code 约定（per Q5 澄清）

**先决条件**：先决条件 §1-§3

**步骤**：

```bash
# Test 1: thread_id_busy → exit 2
#   启动一个 terminal 持有 session，再开 terminal 2 用同一 thread_id:
python -m src.apps.deep_research.cli "..." --thread-id=<existing_busy_id>
echo $?
# 期望: stderr "[ERROR] thread_id_busy: ..." + exit 2

# Test 2: general error → exit 1
#   破坏 env var (LANGGRAPH_CHECKPOINT_DB 不可达):
LANGGRAPH_CHECKPOINT_DB=postgres://invalid:5432/x python -m src.apps.deep_research.cli "..."
echo $?
# 期望: stderr "[ERROR] <type>: <msg>" + exit 1

# Test 3: 缺位置参数 → exit 1
python -m src.apps.deep_research.cli
echo $?
# 期望: stderr usage + exit 1
```

**PASS 信号**：

| Test | 期望 exit | 期望 stderr |
|---|---|---|
| thread_id_busy | **2** | `[ERROR] thread_id_busy: thread_id=<tid> busy; retry later` |
| general error | **1** | `[ERROR] <type>: <msg>` |
| 缺参数 | **1** | `usage: ...` + `[ERROR] missing query argument` |

**FAIL 信号**：
- busy 退出 1 而非 2 → 违反 Q5
- general error 退出 2 而非 1 → 违反 Q5
- 缺参数退出 0 → argparse 没生效

---

## SC-006: CLI thread_id 复用 warning（per Q6 澄清）

**先决条件**：先决条件 §1-§3 + 已存在 checkpoint（如 spec 002 US4 acceptance 跑过 CLI）

**步骤**：

```bash
# 1. 找一个已有 checkpoint 的 thread_id（用 psql 查）:
TID=$(psql $LANGGRAPH_CHECKPOINT_DB -t -c "SELECT thread_id FROM checkpoints LIMIT 1;" | xargs)

# 2. 用该 TID 跑 CLI:
python -m src.apps.deep_research.cli "新 query" --thread-id=$TID
echo $?

# 期望 stderr:
#   [cli] thread_id=<TID>
#   [cli] WARNING: thread_id=<TID> already has <N> checkpoint(s); new run will create a fresh checkpoint and not inherit prior state
#   [cli] query=新 query
# 期望 exit code: 0
```

**PASS 信号**：
- stderr 出现 `[cli] WARNING: thread_id=<TID> already has <N> checkpoint(s); ...`
- exit code = 0

**FAIL 信号**：
- 没有 warning → 检查 `_check_thread_id_reuse` + `SELECT count(*) FROM checkpoints WHERE thread_id = %s`
- exit code != 0 → warning 不应影响 exit

---

## SC-007: Streamlit + pytest 依赖完整性

**先决条件**：先决条件 §1

**步骤**：

```bash
# 1. 验证 streamlit + pytest 4 个新依赖 install 成功
pip show streamlit pytest pytest-cov pytest-asyncio | grep -E "^(Name|Version)"
# 期望:
#   Name: streamlit
#   Version: 1.30.x 或 1.3x.x
#   Name: pytest
#   Version: 8.x
#   Name: pytest-cov
#   Version: 5.x
#   Name: pytest-asyncio
#   Version: 0.23.x 或 0.2x.x

# 2. 验证 CLI 入口可执行
python -m src.apps.deep_research.cli --help
# 期望: argparse usage 打印

# 3. 验证 Streamlit 入口可执行
streamlit run src/apps/deep_research/app.py --server.headless true --server.port 8502 &
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8502
# 期望: 200
kill %1
```

**PASS 信号**：
- 4 个包都已 install + 版本号符合 R2 锁定区间
- CLI `--help` 打印 argparse usage
- Streamlit headless 模式 8502 端口 HTTP 200

**FAIL 信号**：
- 4 个包缺一 → `pip install -e ".[dev]"` 失败
- CLI `--help` 抛 `ModuleNotFoundError` → `src/apps/deep_research/__init__.py` 缺入口
- Streamlit 8502 端口 404/500 → `streamlit run` 命令错

---

## SC-008: pytest 单测 coverage gate（per FR-021 + Principle III）

**先决条件**：先决条件 §1-§3

**步骤**：

```bash
# 1. 跑 test_app_cli.py + 检查 coverage
pytest tests/apps/deep_research/test_app_cli.py \
    --cov=src/apps/deep_research/app.py \
    --cov=src/apps/deep_research/cli.py \
    --cov-report=term-missing \
    --cov-fail-under=80
echo $?

# 期望: 8 passed + coverage >= 80% + exit 0
```

**PASS 信号**：
- 8 个 TC 全 passed
- `app.py` + `cli.py` 两者 coverage 都 >= 80%
- exit code = 0

**FAIL 信号**：
- 任何 TC 失败 → 看 `tests/apps/deep_research/test_app_cli.py` 失败消息
- coverage < 80% → 加 TC（per FR-021）

---

## SC-009: spec 005 增量章节 in README

**先决条件**：先决条件 §1

**步骤**：

```bash
# 1. 验证 README 包含 spec 005 章节
grep -A 10 "## spec 005 增量" src/apps/deep_research/README.md
# 期望: 章节存在 + 列出 app.py / cli.py 入口命令 + 新依赖 + pytest 命令
```

**PASS 信号**：
- README 含 `## spec 005 增量` 章节
- 章节列出：
  - `streamlit run src/apps/deep_research/app.py` 命令
  - `python -m src.apps.deep_research.cli "..."` 命令
  - `streamlit>=1.30.0` + `pytest>=8.0` 等新依赖
  - `pytest --cov-fail-under=80` 测试运行命令

**FAIL 信号**：
- 章节缺失 → 检查 `README.md` 编辑
- 章节缺 pytest 命令 → 章节不完整

---

## 跑完整套 quickstart（10 分钟）

```bash
# Terminal 1: 启动 Postgres / Langfuse（如果未跑）
#   pg_ctl -D /usr/local/var/postgres start
#   或 brew services start postgresql

# Terminal 2: 跑 SC-001 ~ SC-003
streamlit run src/apps/deep_research/app.py

# Terminal 3: 跑 SC-004 ~ SC-006
python -m src.apps.deep_research.cli "为什么 LangGraph 1.2 用 interrupt?"
TID=$(psql $LANGGRAPH_CHECKPOINT_DB -t -c "SELECT thread_id FROM checkpoints LIMIT 1;" | xargs)
python -m src.apps.deep_research.cli "新 query" --thread-id=$TID

# Terminal 4: 跑 SC-007 ~ SC-009
pip show streamlit pytest pytest-cov pytest-asyncio | grep -E "^(Name|Version)"
pytest tests/apps/deep_research/test_app_cli.py --cov=src/apps/deep_research/app.py --cov=src/apps/deep_research/cli.py --cov-fail-under=80
grep -A 10 "## spec 005 增量" src/apps/deep_research/README.md

# 期望: 所有 9 个 SC 全 PASS
```

---

## Quick reference（失败排查）

| 失败症状 | 优先检查 |
|---|---|
| Streamlit 启动报 `ModuleNotFoundError` | `pip install -e ".[dev]"` |
| Plan 页空白 | `format_plan_md` + `__interrupt__` 检测 |
| Approve 后卡死 | `Command(resume="approve")` + conditional edge |
| Final report 空 | `state["final_report"]` 是否被 reducer 覆盖 |
| CLI exit 1 with `[ERROR]` | `run_research` 内部 exception（看 stderr 完整堆栈） |
| CLI exit 2 with `[ERROR] thread_id_busy` | 另一进程持 lock（pg_advisory_xact_lock hashtext(tid)） |
| pytest coverage < 80% | 加 TC（per FR-021） |
| `--thread-id=<existing>` 无 warning | `_check_thread_id_reuse` + `SELECT count(*)` query |