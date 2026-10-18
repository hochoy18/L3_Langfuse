# ADR-0006 · Streamlit UI 作为唯一入口

| 字段 | 值 |
|---|---|
| Status | Accepted |
| Date | 2026-10-18 |
| Scope | `app.py` + 运行入口设计 |

## Context

deep_research agent 的运行入口候选：

1. **CLI**：终端运行，interrupt 时 `input()` 等用户输入
2. **Notebook**：Jupyter cell 演示，interrupt 用 `input()` cell
3. **Streamlit**：单页 Web UI，interrupt 时 `st.button()` + `st.text_area()`
4. **FastAPI + 前端**：REST API + React/Vue 页面

需要支持的核心场景：
- Plan 阶段让用户审阅 / 编辑
- Research 阶段实时看到进度（哪些 Source 已收集）
- Write 阶段实时看到 Draft 在 polish 改进
- 最终报告渲染 Markdown + 下载

## Decision

**采用 Streamlit 单页 UI 作为唯一正式入口**（保留 CLI 作为烟雾测试脚本，标 `internal use only`）。

```bash
# 启动
streamlit run src/apps/deep_research/app.py
```

Streamlit 与 LangGraph interrupt 的集成模式：

```python
# app.py 核心循环 (简化)
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

# 触发 invoke
cmd = Command(resume=st.session_state.pop("user_input", None)) \
      if "user_input" in st.session_state else None
input_state = {"user_query": st.session_state.query} if cmd is None else cmd

result = graph.invoke(input_state, config={"configurable": {"thread_id": st.session_state.thread_id}})

# interrupt 分支
if "__interrupt__" in result:
    plan = result["__interrupt__"][0].value["plan"]
    st.markdown(format_plan_md(plan))
    edits = st.text_area("Edit plan (留空 = approve)")
    if st.button("Submit"):
        st.session_state.user_input = edits or "approve"
        st.rerun()
    st.stop()

# 完成分支
st.markdown(result["final_report"])
```

## Alternatives Considered

### A. CLI

```bash
python -m src.apps.deep_research.cli "为什么..."
```

**否定原因**：
- Plan 阶段用 terminal markdown 渲染体验差（rich 库能美化但仍有局限）
- interrupt 时阻塞 stdin——demo 体验不如点击按钮
- 没有 progress bar（research 阶段跑了多久用户看不到）

### B. Notebook

**否定原因**：
- interrupt 后 cell 状态混乱（需要 magic command）
- 共享难（ipynb 文件不是产品）
- Streamlit 同样能跑 cell（用 `streamlit run notebook.py`）

### D. FastAPI + 前端

**否定原因**：
- 学习项目过度工程化
- 前端开发 / 构建 / 部署成本远超 LangGraph agent 本身
- Streamlit 1.30+ 已经能覆盖 80% deep research UI 需求

## Consequences

**正向**：
- 计划 / 报告天然用 Markdown 渲染（`st.markdown`），交互体验好
- `st.session_state` 与 LangGraph `thread_id` 1:1 对应，interrupt → resume 链路清晰
- `st.empty()` + 异步刷新可显示 research 进度（未来增强）
- 学习曲线低：项目已有 `streamlit` 依赖吗？需要加 `pyproject.toml`

**负向**：
- Streamlit 每次 `st.rerun()` 整个脚本重跑——需要小心 `session_state` 的初始化逻辑
- 多用户 / 鉴权需要额外方案（当前单用户本地用，不考虑）
- 不能直接接 LangGraph Studio（那是另一套 UI）——但 project 已有 Langfuse UI 看 trace

**未来触发条件**：若需要多用户协作 / 远程访问，再评估 FastAPI 或专用前端。

**附**：CLI `cli.py` 保留，仅用于：
- CI 烟雾测试（`pytest` 跑一轮最小 query）
- 不需要 UI 的 headless 环境（如 SSH 服务器）