---
title: 采用不可变的对称模型配置
status: archived
depends_on: [0002-fallback-chain-and-budget]
implemented_in: working-tree
archived_on: 2026-08-09
---

# 归档记录

- **状态**：已归档。
- **决策范围**：主备模型配置的不可变边界、客户端路由语义、运行时状态隔离与旧环境变量迁移。
- **实现位置**：`corecoder/config.py`、`corecoder/fallback.py`、`corecoder/budget.py`、`corecoder/llm.py`、`corecoder/cli.py` 与 `corecoder/session.py`。
- **验证范围**：`tests/test_config.py`、`tests/test_fallback.py`、`tests/test_litellm.py` 与 `tests/test_core.py`。

# 决策

主模型和备用模型均采用同一个完整、不可变的 `ModelConfig`；不可变的 `Config` 只表达启动时解析出的主模型、备用链和进程级限制。CLI 使用 `ModelChain.from_config()` 统一创建主模型与备用模型客户端，确保每一级均使用自身已解析的生成参数。

`backend` 是受限的客户端实现选择器，取值为 `openai_compatible` 或 `litellm`；`provider` 是不限制取值的供应商标识，不再参与客户端路由。`ModelChain` 和独立的 `BudgetState` 保存运行时状态，`/model` 只创建运行时模型名覆盖并重置熔断，不修改启动快照。旧环境变量直接报出迁移错误，以一次不兼容迁移换取唯一、对称且可预测的配置来源。

# 备选方案

- 保留主模型的扁平字段并让备用模型使用独立对象。这会继续保留不同的构造与参数传递路径。
- 让 `provider` 同时选择供应商和客户端。这会把可扩展供应商名称与有限的客户端实现耦合。
- 兼容旧环境变量。这会长期保留两套主模型命名与优先级规则。

# 后果

- `CORECODER_PRIMARY_<FIELD>` 和 `CORECODER_FALLBACK_<N>_<FIELD>` 成为唯一的模型环境变量格式；旧主模型变量与 OpenAI 别名不再生效。
- CLI 生产初始化只经由已解析的 `Config` 和 `ModelChain.from_config()`；直接注入 LLM 对象的模型链构造方式保留给库调用和测试。
- 主模型与备用模型均显式保存 API Key、后端、供应商标识、Base URL 与生成参数，降级后不会丢失参数。
- 会话只保存显式运行时模型名覆盖；预算、熔断和备用激活状态在每次启动时重新建立。
- 启动信息框仅展示 ZorinHelper、主模型和备用模型的名称及 Base URL；完整脱敏诊断信息通过 `Config.summary()` 和 `ModelChain.status()` 获取。
