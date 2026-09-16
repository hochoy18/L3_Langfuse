# hello_trace 的两件事：自定义 OpenAI 客户端 + langfuse.openai 的副作用 import

跑 lesson 0002 hello_trace 时遇到两个非显而易见的问题，都修在同一个文件里。记下来给后面 lesson 引用。

## 1. `langfuse.openai` 拿到的 `openai` 对象默认读 `OPENAI_API_KEY` / `OPENAI_BASE_URL`

用户用的是 MiniMax 兼容协议，env 名是 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL`，**`from langfuse.openai import openai` 拿到的 client 直接读不到**——它内部就是 import 了原生 `openai` 包然后做 monkey-patch，没改 env 解析逻辑。

**修法（任选其一）：**
- 在脚本顶部桥接 env：`os.environ.setdefault("OPENAI_API_KEY", os.getenv("MINIMAX_API_KEY"))`
- 或显式 `OpenAI(api_key=..., base_url=...)` 构造客户端（用户当前采用的方案）

**Why:** 这个细节在 lesson 0002 的 HTML 里没提（默认假设用 OpenAI），但只要切换到非 OpenAI 提供商（Azure / 本地代理 / MiniMax / DeepSeek 等）就一定会撞上。

**How to apply:**
- lesson 0003 之后所有用 LangChain/LangGraph 的脚本，**默认不要 `from langfuse.openai import openai`**，用 LangChain 的 callback handler 就够了（见 lesson 0003）
- lesson 0002 的 HTML 应在末尾加一段"如果你不是用 OpenAI 官方域名……" 的补遗

## 2. `from langfuse.openai import openai` 真正的价值是**副作用 import**

用户为了用自定义 OpenAI client，删掉了 `from langfuse.openai import openai` 这一行。结果：
- `@observe()` 仍然工作 → Trace 节点创建了
- **但 `chat.completions.create` 不再被 patch** → Generation 子节点不会出现，trace 树里只有顶层 span，没有 LLM 调用的输入/输出/tokens

原因：`langfuse.openai` 模块在 import 时调用 `register_tracing()`，用 `wrapt.wrap_function_wrapper` 把 `openai.resources.chat.completions.Completions.create` 替换成带埋点的版本。这个替换发生在 import 阶段，**与客户端怎么构造无关**，但**与模块是否被加载有关**。

**Why:** 是 openai SDK 设计导致的——所有 `OpenAI()` 实例共享同一个 `Completions` 类，所以 patch 一次全局生效。但 patch 必须在 import 时触发，**不能 lazy**。

**How to apply:**
- 任何用 OpenAI 协议调 LLM 的脚本，**即使客户端是自构造的**，也要保留一行 `from langfuse.openai import openai as _lf_openai  # noqa: F401`，纯靠副作用激活 patch
- lesson 0003 给出"标准模板"：使用 LangChain CallbackHandler 时不需要这一行（LangChain 走自己的 callback 系统）；直接调 OpenAI SDK 时必须保留

**Evidence:** 用户在 lesson 0002 跑通后，删掉 import 改用自定义 client，UI 上 trace 只剩顶层 Span，看不到 Generation 子节点。