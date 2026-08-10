# CoreCoder

一个极简 AI coding agent 的核心引擎：LLM 驱动的工具调用循环。本文档记录围绕并发调度、模型降级、MCP 接入三项扩展形成的项目专有词汇。

## Language

**并发安全工具（concurrency-safe tool）**：
`Tool` 的一个布尔属性（`concurrency_safe`），表示该工具的 `execute()` 可以与其他并发安全工具同时运行而不产生副作用冲突。默认 `False`（未声明即视为不安全）。仅 `read_file`、`glob`、`grep` 声明为 `True`；`bash`、`agent`（子 agent）、以及所有 MCP 工具一律视为不安全，不因外部协议自报的信息而改变。
_Avoid_: 只读工具、安全工具（未区分"安全"具体指并发安全还是执行安全）

**分段调度（segmented scheduling）**：
同一批 `tool_calls` 按模型给出的原始顺序切分：连续的并发安全工具归为一段并发执行，任何不安全工具单独成段串行执行。段与段之间按原始顺序前后依赖，保证不安全操作前后的因果顺序不被并发打乱。

**降级链（fallback chain）**：
在主模型经过现有重试机制耗尽后，按顺序尝试的一组备用模型。每一级由完整的模型配置描述，可跨供应商。

**模型配置（model configuration）**：
描述单个可调用模型的不可变配置单元，包含模型标识、认证信息、调用后端、供应商标识、连接地址与生成参数。主模型和备用模型使用相同的配置单元。
_Avoid_: 主模型配置、备用模型配置（两者不是不同的数据模型）

**后端（backend）**：
决定模型请求通过 OpenAI 兼容客户端还是 LiteLLM 客户端发出的受限配置值。后端不表示模型供应商。
_Avoid_: provider、供应商后端

**供应商（provider）**：
模型服务提供方的可扩展标识，用于配置摘要、日志与会话信息，不决定客户端实现。
_Avoid_: backend、客户端类型

**启动配置快照（startup configuration snapshot）**：
在启动时从命令行、系统环境变量和当前目录 `.env` 解析出的不可变配置。它不随交互式模型切换或降级状态改变。

**运行时模型状态（runtime model state）**：
`ModelChain` 在进程内维护的当前生效模型、熔断位置、预算和显式模型名覆盖。该状态不同于启动配置快照。

**熔断（circuit breaker，本项目语境）**：
一旦主模型的重试耗尽并成功降级到降级链的某一级，该级别在本进程剩余生命周期内被记为"当前生效模型"，后续对话不再从主模型重新尝试，除非用户用 `/model` 显式指定新模型（此操作会重置熔断状态）。降级链本身不随 `/model` 改变。

**硬预算上限（hard budget cap）**：
由 `CORECODER_MAX_COST_USD` 环境变量设置的美元金额上限，统计范围为单进程生命周期内的累计估算成本（跨主模型/降级模型分模型累加）。在每轮 `chat()` 发起 LLM 请求前检查；一旦当前累计成本已超过上限，直接拒绝新一轮 LLM 调用并返回提示，不中断已经开始的调用，不终止进程。当前模型不在定价表内（无法估算成本）时放行（fail open）。用户可用 `/budget <金额>` 在不重启进程的前提下当场调高上限。

**MCP 模型可见工具名（model-visible MCP tool name）**：
MCP 工具向模型暴露时使用的稳定名称，格式为 `mcp__<server配置键>__<工具原名>`。server 配置键来自用户提供的 `mcpServers` 配置，不使用 server 自报名称；名称规范化后仍发生冲突时，MCP 配置无效。
_Avoid_: MCP 工具原名、server 自报工具名

**MCP 工具快照（MCP tool snapshot）**：
Agent 启动时从所有成功连接的 MCP server 获取并固定下来的工具集合。运行期间不动态修改该集合；收到工具列表变化通知时，当前快照视为过期，并提示用户重启 CoreCoder。

**MCP Server 配置（MCP server configuration）**：
用户拥有的 `mcpServers` 配置文件，默认位置为 `~/.corecoder/mcp.json`，表示用户授权 CoreCoder 启动其中声明的 stdio server 或连接其中声明的 Streamable HTTP server。CLI 可以临时覆盖或禁用该配置；项目目录及其 `.env` 不会决定启动哪些 MCP server。
_Avoid_: 工作区自动配置、MCP Server 注册表

**MCP 传输（MCP transport）**：
MCP server 配置选择的连接方式。`command` 配置启动本地 stdio server；`url` 配置连接远程 Streamable HTTP server。每个配置只能选择一种传输。
_Avoid_: HTTP MCP 协议、远程 MCP 协议

**Agent 运行状态（Agent runtime state）**：
由单个 `Agent` 持有的当前工作目录和已变更文件集合。该状态在该 Agent 的多次对话之间持续，但不与其他 Agent 共享。
_Avoid_: 运行时模型状态、模块级全局状态
