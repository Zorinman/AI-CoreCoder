# CoreCoder

一个极简 AI coding agent 的核心引擎：LLM 驱动的工具调用循环。本文档记录围绕并发调度、模型降级、MCP 接入三项扩展形成的项目专有词汇。

## Language

**并发安全工具（concurrency-safe tool）**：
`Tool` 的一个布尔属性（`concurrency_safe`），表示该工具的 `execute()` 可以与其他并发安全工具同时运行而不产生副作用冲突。默认 `False`（未声明即视为不安全）。仅 `read_file`、`glob`、`grep` 声明为 `True`；`bash`、`agent`（子 agent）、以及所有 MCP 工具一律视为不安全，不因外部协议自报的信息而改变。
_Avoid_: 只读工具、安全工具（未区分"安全"具体指并发安全还是执行安全）

**分段调度（segmented scheduling）**：
同一批 `tool_calls` 按模型给出的原始顺序切分：连续的并发安全工具归为一段并发执行，任何不安全工具单独成段串行执行。段与段之间按原始顺序前后依赖，保证不安全操作前后的因果顺序不被并发打乱。

**降级链（fallback chain）**：
在主模型经过现有重试机制耗尽后，按顺序尝试的一组备用模型配置，定义在项目根目录的 `corecoder.fallback.json` 中。链中每一级都是完整独立的 `{model, provider, api_key_env, base_url}`，可跨 provider。主模型本身不在这个文件里，仍由 `config.py`/环境变量决定。

**熔断（circuit breaker，本项目语境）**：
一旦主模型的重试耗尽并成功降级到降级链的某一级，该级别在本进程剩余生命周期内被记为"当前生效模型"，后续对话不再从主模型重新尝试，除非用户用 `/model` 显式指定新模型（此操作会重置熔断状态）。降级链本身不随 `/model` 改变。

**硬预算上限（hard budget cap）**：
由 `CORECODER_MAX_COST_USD` 环境变量设置的美元金额上限，统计范围为单进程生命周期内的累计估算成本（跨主模型/降级模型分模型累加）。在每轮 `chat()` 发起 LLM 请求前检查；一旦当前累计成本已超过上限，直接拒绝新一轮 LLM 调用并返回提示，不中断已经开始的调用，不终止进程。当前模型不在定价表内（无法估算成本）时放行（fail open）。用户可用 `/budget <金额>` 在不重启进程的前提下当场调高上限。

**MCP 工具前缀（`mcp_corecoder__`）**：
所有从 MCP server 拉取的工具，注册到 Agent 时统一重命名为 `mcp_corecoder__<server名>__<工具原名>`（双下划线分隔），避免与内置工具或多个 MCP server 之间的工具名冲突。
