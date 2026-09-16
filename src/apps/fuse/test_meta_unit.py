"""meta.py 单元自测：纯数据结构层验证，不需要 Langfuse 服务端。

跑法::

    uv run python src/apps/fuse/test_meta_unit.py

期望：每个 case 打印 ✓，最终 ALL PASS。
"""

from __future__ import annotations

import os

from src.apps.fuse.meta import (
    AgentMeta,
    BusinessMeta,
    ExperimentMeta,
    IdentityConfig,
    ModelMeta,
    RuntimeMeta,
    TraceMetadata,
)


def test_identity_alias_roundtrip() -> None:
    cfg = IdentityConfig(
        session_id="s", user_id="u", tags=["a", "b"], trace_name="t"
    )
    d = cfg.model_dump(exclude_none=True, by_alias=True)
    assert d == {
        "langfuse_session_id": "s",
        "langfuse_user_id": "u",
        "langfuse_tags": ["a", "b"],
        "langfuse_trace_name": "t",
    }, f"alias roundtrip 失败：{d}"
    print("✓ identity alias roundtrip")


def test_extra_allow_custom_fields() -> None:
    meta = TraceMetadata(
        business=BusinessMeta(parent_agent_id="a-007", delegation_reason="timeout"),
        runtime=RuntimeMeta(environment="prod", cloud_region="us-east-1"),
    )
    d = meta.to_metadata_dict()
    assert d["parent_agent_id"] == "a-007", d
    assert d["delegation_reason"] == "timeout", d
    assert d["cloud_region"] == "us-east-1", d
    print("✓ extra='allow' 自定义字段")


def test_none_values_stripped() -> None:
    meta = TraceMetadata(
        agent=AgentMeta(agent_name="x"),  # version/strategy 留空
    )
    d = meta.to_metadata_dict()
    assert d["agent_name"] == "x", d
    assert "agent_version" not in d, d
    assert "agent_strategy" not in d, d
    print("✓ None 字段被剔除")


def test_to_langchain_config_shape() -> None:
    meta = TraceMetadata(
        identity=IdentityConfig(session_id="s1", tags=["prod"]),
        agent=AgentMeta(agent_name="a"),
    )
    cfg = meta.to_langchain_config(callbacks=["<sentinel>"])  # 字符串当哨兵
    assert "callbacks" in cfg and cfg["callbacks"] == ["<sentinel>"], cfg
    assert cfg["metadata"]["langfuse_session_id"] == "s1", cfg
    assert cfg["metadata"]["langfuse_tags"] == ["prod"], cfg
    assert cfg["metadata"]["agent_name"] == "a", cfg
    assert "agent_version" not in cfg["metadata"], cfg
    print("✓ to_langchain_config 形态正确")


def test_env_from_env_helper() -> None:
    os.environ["ENV"] = "staging"
    meta = TraceMetadata.from_env(agent=AgentMeta(agent_name="x"))
    assert meta.runtime.environment == "staging", meta.runtime
    # 调用方传了 runtime 时不应被环境变量覆盖
    meta2 = TraceMetadata.from_env(
        agent=AgentMeta(agent_name="y"),
        runtime=RuntimeMeta(environment="prod"),
    )
    assert meta2.runtime.environment == "prod", meta2.runtime
    print("✓ from_env() 自动取 ENV 环境变量（且不覆盖显式传入）")


def test_environment_regex_warning() -> None:
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        RuntimeMeta(environment="Prod")  # 大写 P，不符合正则
    matched = [w for w in caught if "environment" in str(w.message)]
    assert matched, f"expected warning, got {[str(w.message) for w in caught]}"
    print("✓ environment 字段格式不符会 warn")


def test_identityconfig_tags_strict_list() -> None:
    from pydantic import ValidationError

    try:
        IdentityConfig(tags="prod,staging")  # 字符串而不是 list
    except ValidationError:
        print("✓ tags 字段类型不符直接 ValidationError")
        return
    raise AssertionError("expected ValidationError when tags is str")


def test_full_composition() -> None:
    """完整示例 + 验证输出顺序和形态。"""
    meta = TraceMetadata(
        identity=IdentityConfig(
            session_id="sess-abc",
            user_id="u-9f3a",
            tags=["prod", "agent"],
            trace_name="checkout-recommend-v2",
        ),
        agent=AgentMeta(
            agent_name="checkout-recommender",
            agent_version="2.1.0",
            agent_strategy="react",
        ),
        runtime=RuntimeMeta(
            environment="prod",
            app_version="1.4.2",
            deployment_id="pod-7f3a",
            request_id="req-001",
            input_source="api",
        ),
        experiment=ExperimentMeta(
            experiment_bucket="treatment-A",
            variant_id="v2.1",
            feature_flags={"new_ranker": True},
        ),
        model=ModelMeta(temperature=0.3, max_tokens=512, model_name="MiniMax-M3"),
        business=BusinessMeta(customer_tier="gold", cart_value=1280.0),
    )
    d = meta.to_metadata_dict()
    # 4 个 langfuse_* 顶级字段都在
    assert d["langfuse_session_id"] == "sess-abc"
    assert d["langfuse_user_id"] == "u-9f3a"
    assert d["langfuse_tags"] == ["prod", "agent"]
    assert d["langfuse_trace_name"] == "checkout-recommend-v2"
    # 业务字段都在
    assert d["agent_name"] == "checkout-recommender"
    assert d["environment"] == "prod"
    assert d["feature_flags"] == {"new_ranker": True}
    assert d["customer_tier"] == "gold"
    print("✓ 完整组合 6 分类输出正确")


if __name__ == "__main__":
    test_identity_alias_roundtrip()
    test_extra_allow_custom_fields()
    test_none_values_stripped()
    test_to_langchain_config_shape()
    test_env_from_env_helper()
    test_environment_regex_warning()
    test_identityconfig_tags_strict_list()
    test_full_composition()
    print("\nALL PASS")