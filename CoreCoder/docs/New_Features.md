# New Features

记录在 `article/` 原教程之外，为本项目新增的扩展功能。每个功能对应
`docs/specs/` 下的一份 spec 和 `docs/adr/` 下的一份 ADR。

## 1. 并发安全分段调度（concurrency-safe segmented scheduling）

对应 spec：[docs/specs/0001-concurrency-scheduling.md](specs/0001-concurrency-scheduling.md)
对应 ADR：[docs/adr/0001-concurrency-safety-whitelist.md](adr/0001-concurrency-safety-whitelist.md)

**状态**：已归档（实现提交 `d8a25c4`，2026-08-08；归档日期 2026-08-09）

### 问题

原实现里，`Agent` 收到多个 `tool_calls` 时只要数量大于 1 就统一丢进
`ThreadPoolExecutor` 并发执行，不区分工具是否有副作用。`bash`、
`edit_file`、`write_file` 这类会产生跨调用共享状态或副作用的工具，被并发调
用时可能出现结果覆盖、顺序错乱等竞态问题。

### 方案

- 给 `Tool` 基类增加类属性 `concurrency_safe: bool = False`（默认拒绝，
  未声明即视为不安全）。
- 只读、无共享可变状态的工具显式声明为 `True`：`read_file`、`glob`、
  `grep`。
- `bash`、`agent`（子 agent）保持默认（不安全），不引入命令前缀解析或白名
  单去细化 `bash` 的部分安全性。
- `Agent` 执行一批 `tool_calls` 时按原始顺序做"分段"：连续的安全调用聚合成
  一段并发执行，每个不安全调用单独成一段串行执行；段与段之间严格按原始出
  现顺序执行，保证不安全操作前后的因果顺序不被打乱。
- 返回结果列表顺序始终与原始 `tool_calls` 顺序一致，不因分段调度而改变。

### 代码变化

- `corecoder/tools/base.py`：`Tool` 基类新增 `concurrency_safe = False`。
- `corecoder/tools/read.py`、`glob_tool.py`、`grep.py`：声明
  `concurrency_safe = True`。
- `corecoder/agent.py`：新增 `_tool_is_safe()`、`_segment_tool_calls()`；
  改写 `_exec_tools_parallel()`，按分段结果决定每段用线程池并发还是直接串
  行执行。
- `tests/test_core.py`：新增分段调度相关的单元测试（分段结果、安全段确实
  并发重叠、不安全段严格无重叠、混合批次结果顺序保持一致）。

## 2. 统一主备模型配置（unified primary and fallback model configuration）

对应归档 Spec：[docs/archives/0004-unified-model-configuration.md](archives/0004-unified-model-configuration.md)
对应归档 ADR：[docs/archives/0004-immutable-symmetric-model-configuration.md](archives/0004-immutable-symmetric-model-configuration.md)

**状态**：已实现，待提交

### 问题

主模型使用扁平字段，备用模型使用独立对象，二者的 provider 路由和生成参数传递不一致。旧环境变量、OpenAI 别名和备用链变量共存，配置来源与实际生效状态不易直接辨别。

### 方案

- 主模型与备用模型统一为不可变的 `ModelConfig`，每一级都包含模型、密钥、`backend`、`provider`、Base URL、temperature 和 max tokens。
- `Config` 是包含主模型、备用链和进程级限制的不可变启动快照；`ModelChain` 只保存熔断、预算和运行时模型名覆盖。
- 仅使用 `CORECODER_PRIMARY_<FIELD>` 与 `CORECODER_FALLBACK_<N>_<FIELD>` 两种对称模型变量格式。每一级的 `MODEL`、`API_KEY` 必填，备用级别从 1 开始连续编号。
- `backend` 受限为 `openai_compatible` 或 `litellm`，`provider` 保持自由供应商标识，不再决定客户端类型。
- 配置优先级为 CLI 主模型参数、系统环境变量、当前目录 `.env`、默认值；旧变量会产生包含迁移目标的配置错误。
- 主备模型由 `ModelChain.from_config()` 通过同一个工厂创建，因此生成参数不会在降级后丢失。
- `LLM` 通过公开的 `has_known_cost` 能力声明当前模型是否具备定价数据；模型链不再读取 LLM 模块的私有价格表。
- `BudgetState` 位于独立的预算模块中，模型链只负责根据预算状态执行跨模型准入检查。

### 可见性与边界

- CLI 启动信息框以 ZorinHelper 名称显示，仅展示主模型和各备用模型的名称与 Base URL；不展示密钥、provider、backend、预算或其他配置。`ModelChain.status()` 显示备用链、当前激活级别和最近失败类型。
- `/model` 只更改运行时主模型名并重置熔断，不修改启动配置、连接信息或备用链。
- 会话只保存消息和显式主模型名覆盖，不保存备用级别、熔断状态或预算累计。
- `.env.example` 包含全部正式字段，备用模型块默认注释，避免复制模板后启用不完整的备用级别。

### 验证

- 自动化测试覆盖环境变量与程序化构造等价、主备参数转发、严格校验、旧变量迁移错误、脱敏输出和运行时状态边界。
