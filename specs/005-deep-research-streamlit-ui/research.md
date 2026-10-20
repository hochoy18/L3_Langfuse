# Research: Deep Research Streamlit UI (spec 005)

**Purpose**: Resolve technical unknowns before Phase 1 design. Each research item lists Decision / Rationale / Alternatives considered.

**Created**: 2026-10-19

---

## R1. Streamlit `app.py` 单测模式选型

**Unknown**: `app.py` 单测如何模拟 Streamlit session_state + 触发 button click + assert 渲染输出？两种主流路径：
- **Path A**：`streamlit.testing.v1.AppTest`（Streamlit 1.28+ 官方推荐 framework）——`from streamlit.testing.v1 import AppTest; at = AppTest.from_file("app.py"); at.run()` 返回 AppTest 对象，可访问 `.sidebar` / `.main` / `.button` / `.markdown` / `.warning` / `.error` / `.success` / `.spinner` 等。
- **Path B**：monkeypatch 替换 `st.text_input` / `st.button` / `st.markdown` / `st.spinner` 等——需要为每个 `st.*` 调用 stub。

**Decision**: **Path A**（`AppTest`）用于 button / text_input / markdown 渲染分支；**Path B**（monkeypatch）仅用于 `_run_research_with_stages` 这种**不依赖 Streamlit 完整生命周期**的纯函数包装器。

**Rationale**:
1. AppTest 是 Streamlit 官方推荐的单测 framework（Streamlit 1.28+ release notes），模拟 `streamlit run` 的真实生命周期（rerun / session_state / widget state），不需要写 monkeypatch stub。
2. AppTest 的 `at.button[0].click()` 自动触发 `.run()` 并 rebind session_state，比手动 `st.session_state.user_input = ...; st.rerun()` 更接近真实 browser 行为。
3. 纯函数包装器（`_run_research_with_stages(input_payload, thread_id) -> dict`）**不依赖** Streamlit 生命周期，monkeypatch 让 unit test 更快 + 不需要 AppTest 的 `from_file` IO 开销。
4. AppTest 在 CI 友好——不需要 headless browser / xvfb，纯 Python in-process 跑。

**Alternatives considered**:
- **全 monkeypatch 替换 `st.*`**（FR-022 草案）——实现量大（每个 `st.*` 调用 stub）、易遗漏（`st.cache_data` / `st.experimental_rerun` 等冷门 API）、无法测真实 rerun 行为。**不采用**。
- **Selenium / Playwright 端到端**——超过 spec 005 scope（per FR-020 "不引入 SSE / websocket / 实时协作"），CI 启动 overhead 过高。**不采用**。

**Implementation notes**:
```python
# test_app_cli.py 示例（TC-1: __interrupt__ 分支）
from streamlit.testing.v1 import AppTest

def test_interrupt_renders_plan_review():
    at = AppTest.from_file("src/apps/deep_research/app.py")
    at.session_state["thread_id"] = "test-int-001"
    at.text_input[0].set_value("为什么 LangGraph 1.2 用 interrupt").run()
    # monkeypatch run_research 返回 __interrupt__ payload
    at.run()
    # assert 渲染了 plan markdown + Approve / Submit Edit button
    assert any("Plan 审阅" in str(md.value) for md in at.markdown)
    assert at.button  # 至少一个 button
```

---

## R2. pytest + pytest-cov + pytest-asyncio + Streamlit 1.30 版本兼容性

**Unknown**: 项目首次引入 pytest + pytest-cov + pytest-asyncio（per FR-016 + Principle III），需要确认 4 个库与 Python 3.11+ / Streamlit 1.30+ / psycopg 3.3.5+ 兼容。

**Decision**: 锁定版本区间：
- `pytest>=8.0,<9.0`（pytest 8 是 2024-01 stable；9.x 是 future，未发布）
- `pytest-cov>=5.0,<6.0`（pytest-cov 5 是 2024-04 stable，与 pytest 8 配套）
- `pytest-asyncio>=0.23,<1.0`（pytest-asyncio 0.23 是 2024-04 stable；与 pytest 8 兼容）
- `streamlit>=1.30.0,<2.0`（1.30 是 ADR-0006 决策时刻版本；2.x 是 future）

**Rationale**:
1. **pytest 8 / pytest-cov 5 / pytest-asyncio 0.23** 三者**同一时期发布**，配套稳定；任意一个 lock 到旧版都会触发"另一 lib 强制版本"冲突。
2. **streamlit 1.30** 在 `streamlit.testing.v1.AppTest` 1.28 引入后 2 个 minor 版本发布，社区反馈稳定；AppTest 1.30+ 有小修复（如 `at.session_state` 默认值 setter）。
3. **Python 3.11+**：项目 `pyproject.toml::requires-python = ">=3.11"`，实装环境 Python 3.13.13；4 个库均支持 3.11+（pytest 8 移除了 3.7 / 3.8 支持但保留 3.9-3.13；streamlit 1.30 支持 3.9-3.13）。
4. **psycopg[binary] 3.3.5**（spec 001 已锁定）与 4 个新 lib 无依赖冲突（pytest / streamlit 都不直接依赖 psycopg）。
5. **CI 友好**：4 个 lib 在 PyPI mirror 都可访问，无 private index 需求。

**Alternatives considered**:
- **pytest 7 + pytest-cov 4 + pytest-asyncio 0.21**（旧版组合）——pytest 7 已 EOL（2024-01），CI 报警；新版有 strict-markers / asyncio_mode auto 改进。**不采用**。
- **streamlit < 1.30**（如 1.28）——AppTest 1.28 引入，但 1.28 有 known issue：`at.session_state` 不能在 `set_value` 前读取；锁定 1.30 避免此坑。**不采用**。

**Validation plan**:
- `pip install -e .` 后 `pytest --version` + `streamlit --version` 都可执行（per SC-008）
- 在干净 venv 内 install：`streamlit>=1.30.0` + 3 个 pytest dev deps 不报错（per SC-008）
- `pytest --collect-only tests/apps/deep_research/test_app_cli.py` 能识别 8 个 TC

---

## R3. `_run_research_with_stages` 包装器 UX 边界（per Q4 澄清）

**Unknown**: 在 LangGraph `graph.invoke()` 阻塞模型 + 无 sub-graph callback hook 的现状下，UI 端如何在 invoke 期间给 User 反馈（per FR-010 + Q4 澄清）？

**Decision**: **单段 `st.spinner("研究进行中...")` + invoke 完成后 single-shot `st.empty().markdown("✅ 完成")`**。**不**做"Plan 生成中 → Research 中 → Write 中 → 完成"4 阶段切。

**Rationale**（per Q4 澄清，2026-10-19）：
1. **阻塞 invoke 模型**：LangGraph 1.2.x 的 `graph.invoke()` 是同步阻塞到 END 才返回；调用线程在 `plan_node` → `confirm_plan_node` interrupt → resume → `research_subgraph` → `write_subgraph` → `emit_final_report` 整条链上都不放控制权。UI 线程无法在 Plan → Research 边界**真实**插入文字切换。
2. **fake placeholder 切换是 dead code**：要在 invoke 阻塞期间切换文字，要么 (a) 用 LangGraph NodeCallEvent 回调（spec 006 Langfuse hooks 范畴，**超出 spec 005 scope**），要么 (b) 用 `asyncio.wait` 把 invoke 跑在后台线程 + UI 线程轮询 state（引入并发原语，超出 spec 005 scope）。两条都会**虚假**制造阶段切换，User 看到的 "Research 中..." 实际可能在 `plan_node` 的 30s LLM 调用中。
3. **单段 spinner + 完成收尾是诚实反馈**：User 看到 spinner 旋转 = 图在跑；invoke 完成 = spinner 区切到 "✅ 完成"；这是阻塞模型下**真实**的状态信号。
4. **Lesson 0007 6+ 分钟 research loop 不焦虑**：spinner 旋转 + 完成收尾足以消除 "页面卡死？" 焦虑——Lesson 0007 实验脚本通过 stderr / CLI exit code 监控进度，不需要 UI 实时推送。

**Alternatives considered**:
- **4 阶段 placeholder 切换（Q4 决策前的草案）**——如上所述，会引入 fake 切换或 callback hook 越界。**不采用**。
- **轮询 `graph.get_state()` 后台线程**——需要后台 invoke 线程，破坏 LangGraph 1.2.x 同步语义，且 callback hook 仍是 spec 006 范畴。**不采用**。

**Implementation notes**:
```python
# app.py::_run_research_with_stages
import streamlit as st
from src.apps.deep_research.graph import run_research

def _run_research_with_stages(input_payload, thread_id: str) -> dict:
    with st.spinner("研究进行中..."):
        if input_payload is None:
            input_payload = {"user_query": st.session_state.user_query}
        result = run_research(input_payload, thread_id=thread_id)
    # Invoke 完成后 single-shot 收尾（仅在非 interrupt / 非 error 时）
    if isinstance(result, dict) and not result.get("__interrupt__") and not result.get("error"):
        st.markdown("✅ 完成")
    return result
```

---

## R4. CLI thread_id 复用 warning 的检测方式（per Q6 澄清）

**Unknown**: CLI `--thread-id=<existing_tid>` 已存在 Postgres `checkpoints` 时，warning 的"是否已存在"检测如何做（per FR-014 + Q6 澄清）？

**Decision**: **invoke 前 `SELECT count(*) FROM checkpoints WHERE thread_id = :tid`，N > 0 打印 warning。** 通过 `psycopg.connect(LANGGRAPH_CHECKPOINT_DB)` 单次只读 query，2-3 行代码，不引入额外依赖。

**Rationale**:
1. **`psycopg` 已锁定**（per spec 001）：CLI 进程内已有 `psycopg[binary]>=3.3.5`，无需 `psycopg2` / `asyncpg` 等其他 driver。
2. **`LANGGRAPH_CHECKPOINT_DB` env var 已存在**（per spec 001 + ADR-0005）：CLI 与 Streamlit / `run_research` 共享同一连接串，无新 env var。
3. **`checkpoints` 表 schema 由 LangGraph `PostgresSaver.setup()` 自动创建**（per spec 001 SC-004）：`thread_id` 列已存在；`SELECT count(*) WHERE thread_id = :tid` 是 LangGraph 官方推荐的 thread 存在性查询（见 LangGraph docs）。
4. **sync `psycopg.connect` P50 < 50ms**（本地 Postgres）：CLI startup overhead 可忽略；不引入 async IO。
5. **不引入 psycopg.errors 异常处理**——`psycopg.OperationalError`（Postgres 不可达）让 CLI 走 FR-013 通用 error 路径（exit 1），与 `run_research` 内部 connect 行为对齐。

**Alternatives considered**:
- **让 `run_research` 返回 warning metadata**（如 `result["__warnings__"] = ["thread_id_reused"]`）——需要修改 spec 001 `run_research` 签名，**违反 FR-017 "不改 graph.py"**。**不采用**。
- **跳过 warning 检查**——Dev 不可见"我在聚合 trace"，与 Q6 澄清目标冲突。**不采用**。
- **Streamlit UI 写 warning**——UI 已经在 `Load thread` 流程中显示 thread_id 文本框，User 视觉上可见，无需额外 warning。**不采用**（CLI 单独做 warning 是因为 CLI 没有视觉反馈）。

**Implementation notes**:
```python
# cli.py::main() 启动时检测
import psycopg
from src.apps.deep_research.graph import DEFAULT_DB_URI

def _check_thread_id_reuse(tid: str) -> int:
    """Return count of existing checkpoints for tid; 0 if none."""
    uri = os.environ.get("LANGGRAPH_CHECKPOINT_DB", DEFAULT_DB_URI)
    try:
        with psycopg.connect(uri, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (tid,))
                return cur.fetchone()[0] or 0
    except psycopg.OperationalError:
        # Postgres 不可达 — 让后续 run_research 报 connection error（统一错误路径）
        return 0

def main():
    args = parse_args()
    tid = args.thread_id or str(uuid.uuid4())
    print(f"[cli] thread_id={tid}", file=sys.stderr)
    print(f"[cli] query={args.query[:60]}", file=sys.stderr)

    n = _check_thread_id_reuse(tid)
    if n > 0:
        print(
            f"[cli] WARNING: thread_id={tid} already has {n} checkpoint(s); "
            f"new run will create a fresh checkpoint and not inherit prior state",
            file=sys.stderr,
        )
    # ... invoke run_research
```

---

## 总结：4 个技术 unknown 全部 resolved

| Unknown | Decision | 关键依据 |
|---|---|---|
| R1 (Streamlit 单测模式) | AppTest (Streamlit 1.28+) + 局部 monkeypatch | 官方推荐 + rerun 行为真实 |
| R2 (依赖版本锁定) | streamlit>=1.30,<2.0 + pytest 8 + pytest-cov 5 + pytest-asyncio 0.23 | 同期发布 + 与 Python 3.11+ 兼容 |
| R3 (invoke UX 边界) | 单段 spinner + 完成 single-shot 收尾 | 阻塞模型诚实反馈（per Q4） |
| R4 (thread_id warning 检测) | `psycopg.connect` + `SELECT count(*) FROM checkpoints WHERE thread_id = :tid` | 已有 psycopg + LangGraph 官方推荐查询 |

无未 resolved 的 NEEDS CLARIFICATION。Phase 1 design 可直接落地。