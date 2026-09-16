"""类型安全的 Langfuse metadata 配置层。

把分散在 LangChain ``config["metadata"]`` 里的字符串字典收敛成分类配置。
6 大分类 + IdentityConfig + TraceMetadata 组合类。

设计目标
--------
1. **类型安全**：Pydantic BaseModel 在构造时校验字段类型和拼写，IDE 完整补全。
2. **自定义字段**：`ConfigDict(extra="allow")` 让每个分类都能挂任意业务字段。
3. **一行接入**：`TraceMetadata.to_langchain_config(callbacks=[handler])`
   直接喂给 ``agent.invoke(input, config=...)``。

使用示例
--------
>>> meta = TraceMetadata(
...     identity=IdentityConfig(
...         session_id="sess-abc",
...         user_id="u-9f3a",
...         tags=["prod", "agent"],
...         trace_name="checkout-recommend-v2",
...     ),
...     agent=AgentMeta(agent_name="checkout-recommender", agent_version="2.1.0"),
...     runtime=RuntimeMeta(environment="prod", deployment_id="pod-7f3a"),
...     business=BusinessMeta(customer_tier="gold", cart_value=1280),
... )
>>> meta.to_metadata_dict()
{'langfuse_session_id': 'sess-abc', ..., 'customer_tier': 'gold', ...}
>>> meta.to_langchain_config(callbacks=[handler])
{'callbacks': [...], 'metadata': {...}}
"""

from __future__ import annotations

import os
import re
import warnings
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# 1) 底层基类：所有分类类都继承它
# ---------------------------------------------------------------------------


class _BaseMeta(BaseModel):
    """所有 meta 子类的基类。

    - ``extra="allow"``：调用方可传任意自定义字段（feature_flags、order_id …）。
    - ``populate_by_name=True``：alias 和字段名都可用。
    - ``to_dict()``：默认 ``exclude_none=True``，避免把 None 灌进 trace.metadata。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def to_dict(self) -> Dict[str, Any]:
        """转纯 dict，跳过 None。"""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# 2) IdentityConfig：4 个 langfuse_* 顶级字段
# ---------------------------------------------------------------------------


class IdentityConfig(_BaseMeta):
    """会进入 LangChain metadata 的 4 个 langfuse_* 顶级字段。

    CallbackHandler 识别规则（见 langfuse CallbackHandler.py 的
    ``_parse_langfuse_trace_attributes``）：

    - ``langfuse_session_id`` → trace.session_id（必须是 ``str``）
    - ``langfuse_user_id`` → trace.user_id（必须是 ``str``）
    - ``langfuse_tags`` → trace.tags（必须为 ``list``）
    - ``langfuse_trace_name`` → trace.name（必须是 ``str``）

    字段命名故意用 ``alias``——调用方写 ``session_id=...`` 就行，
    序列化时自动加 ``langfuse_`` 前缀，防止拼错。
    """

    session_id: Optional[str] = Field(
        default=None,
        alias="langfuse_session_id",
        description="trace.session_id（多轮聚合）",
    )
    user_id: Optional[str] = Field(
        default=None,
        alias="langfuse_user_id",
        description="trace.user_id（用户级聚合）",
    )
    tags: Optional[List[str]] = Field(
        default=None,
        alias="langfuse_tags",
        description="trace.tags（与 LangChain 顶层 tags 合并去重）",
    )
    trace_name: Optional[str] = Field(
        default=None,
        alias="langfuse_trace_name",
        description="trace.name（覆盖默认类名）",
    )


# ---------------------------------------------------------------------------
# 3) AgentMeta：agent 自身信息
# ---------------------------------------------------------------------------


class AgentMeta(_BaseMeta):
    """agent 自身信息：名字、版本、策略。"""

    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    agent_strategy: Optional[str] = None


# ---------------------------------------------------------------------------
# 4) RuntimeMeta：环境 + 请求
# ---------------------------------------------------------------------------


class RuntimeMeta(_BaseMeta):
    """运行时环境 + 单次请求的上下文。"""

    environment: Optional[str] = None
    app_version: Optional[str] = None
    deployment_id: Optional[str] = None
    request_id: Optional[str] = None
    input_source: Optional[str] = None

    @model_validator(mode="after")
    def _warn_environment(self) -> "RuntimeMeta":
        """environment 字段格式警告。

        Langfuse 服务端对 ``environment`` 有正则约束
        （小写字母数字 + ``-`` / ``_``，≤40 chars，不得以 ``langfuse`` 开头）。
        走 LangChain metadata 这条路时服务端不会强制校验，但若有人把它复制到
        ``propagate_attributes(environment=...)`` 就会被服务端拒绝——这里只 warn 不 raise。
        """
        if self.environment is not None:
            pattern = re.compile(r"^[a-z0-9_-]{1,40}$")
            if not pattern.match(self.environment) or self.environment.startswith(
                "langfuse"
            ):
                warnings.warn(
                    f"environment={self.environment!r} 不符合 Langfuse 服务端正则"
                    f"（小写字母数字 + -_，≤40 chars，不以 langfuse 开头）",
                    stacklevel=2,
                )
        return self


# ---------------------------------------------------------------------------
# 5) ExperimentMeta：A/B + 灰度
# ---------------------------------------------------------------------------


class ExperimentMeta(_BaseMeta):
    """A/B 实验 / 灰度发布相关。"""

    experiment_bucket: Optional[str] = None
    variant_id: Optional[str] = None
    feature_flags: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 6) ModelMeta：LLM 参数
# ---------------------------------------------------------------------------


class ModelMeta(_BaseMeta):
    """LLM 调用参数。同一模型不同参数下的对比必备。"""

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    model_name: Optional[str] = None


# ---------------------------------------------------------------------------
# 7) BusinessMeta：完全自定义
# ---------------------------------------------------------------------------


class BusinessMeta(_BaseMeta):
    """业务自定义 metadata。

    无任何固定字段，纯靠 ``extra="allow"`` 接任意业务字段
    （product_id / order_id / customer_tier / parent_agent_id …）。
    """


# ---------------------------------------------------------------------------
# 8) TraceMetadata：组合类
# ---------------------------------------------------------------------------


class TraceMetadata(_BaseMeta):
    """6 分类的容器。唯一对外暴露的组合类。

    典型三段式::

        meta = TraceMetadata(
            identity=IdentityConfig(session_id="sess-1", tags=["prod"]),
            agent=AgentMeta(agent_name="checkout-recommender", agent_version="2.1.0"),
            runtime=RuntimeMeta(environment="prod", deployment_id="pod-7f3a"),
        )
        config = meta.to_langchain_config(callbacks=[handler])
        agent.invoke(input, config=config)
    """

    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    agent: AgentMeta = Field(default_factory=AgentMeta)
    runtime: RuntimeMeta = Field(default_factory=RuntimeMeta)
    experiment: ExperimentMeta = Field(default_factory=ExperimentMeta)
    model: ModelMeta = Field(default_factory=ModelMeta)
    business: BusinessMeta = Field(default_factory=BusinessMeta)

    # ---------- 序列化 ----------

    def to_metadata_dict(self) -> Dict[str, Any]:
        """合成一个 dict，喂给 LangChain ``config['metadata']``。

        IdentityConfig 用 ``by_alias=True`` 输出 ``langfuse_*`` 前缀；
        其余分类平铺到顶层，最终合并。
        """
        out: Dict[str, Any] = {}
        identity_dump = self.identity.model_dump(exclude_none=True, by_alias=True)
        out.update(identity_dump)
        for sub in (
            self.agent,
            self.runtime,
            self.experiment,
            self.model,
            self.business,
        ):
            out.update(sub.to_dict())
        return out

    # ---------- LangChain 入口 ----------

    def to_langchain_config(self, callbacks: List[Any]) -> Dict[str, Any]:
        """返回 LangChain ``agent.invoke(input, config=...)`` 用的 config dict。

        Parameters
        ----------
        callbacks
            通常是 ``[CallbackHandler()]``，也允许同时挂多个 callback
            （如 ``[CallbackHandler(), ConsoleCallbackHandler()]``）。
        """
        return {
            "callbacks": callbacks,
            "metadata": self.to_metadata_dict(),
        }

    # ---------- 别名：给手工 update_current_xxx 用 ----------

    def to_flat_dict(self) -> Dict[str, Any]:
        """等价于 ``to_metadata_dict()``；命名上更口语，给非 LangChain 入口用。

        例如::

            lf = get_client()
            lf.update_current_span(metadata=meta.to_flat_dict())
        """
        return self.to_metadata_dict()

    # ---------- 便利：从环境变量自动取 environment ----------

    @classmethod
    def from_env(
        cls,
        *,
        env_var: str = "ENV",
        default_env: str = "dev",
        **kwargs: Any,
    ) -> "TraceMetadata":
        """从环境变量预填 ``environment``，其余字段照传。

        用法::

            meta = TraceMetadata.from_env(
                agent=AgentMeta(agent_name="x"),
                runtime=RuntimeMeta(deployment_id="pod-7f3a"),
            )
        """
        runtime = kwargs.pop("runtime", RuntimeMeta())
        if runtime.environment is None:
            runtime = runtime.model_copy(
                update={"environment": os.getenv(env_var, default_env)}
            )
        return cls(runtime=runtime, **kwargs)