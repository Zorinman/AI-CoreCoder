# MCP 只做 client 端，且不信任服务器自报的并发安全 hint

CoreCoder 只消费外部 MCP server 的工具（client），不将自身能力暴露为 MCP server。发现与连接逻辑放在 `cli.py` 层：启动时若 cwd 存在 `./.mcp.json`（复用 Claude Code 的 `mcpServers` 配置格式）就自动连接列出的 server，连接失败的单个 server 跳过、其余照常工作；连接建立后把包装好的 `McpTool` 实例列表传入 `Agent(tools=...)`。`Agent` 类本身保持"纯接受 tools 列表"，不在内部主动发现/连接外部进程——这样 `Agent` 作为库使用（如文章07提到的"只读审查 agent"场景）时不会意外启动任何外部进程。

MCP 协议的 tool annotations（`readOnlyHint`/`destructiveHint` 等）在规范中明确是服务器自报、不保证准确的 hint。所有 MCP 工具因此一律归入"不安全"（不可并发、纳入分段调度的串行段），不依据这些 hint 放宽并发限制——这与 ADR 0001 中"无法静态判定就默认不安全"的原则一致，把信任边界画在了"我们自己审查过的内置工具"和"外部不可控代码"之间。

MCP 工具名统一加前缀 `mcp_corecoder__<server名>__<工具原名>`（双下划线），避免与内置工具及不同 MCP server 之间的工具名冲突。

同步/异步桥接：MCP Python SDK 是异步的（`async with Client`），而 CoreCoder 现有 `Tool.execute()` 是同步接口，且已用 `ThreadPoolExecutor` 做并发调度。为不改动 `Tool` 接口和现有并发方案，每个 MCP server 连接绑定一个独立的、常驻的 asyncio 事件循环线程（生命周期与 `Agent` 实例绑定：起 Agent 时连接，析构/退出时断开），`McpTool.execute()` 内部用 `asyncio.run_coroutine_threadsafe` 把调用提交到该线程并阻塞等待结果。

子 agent（`AgentTool` 创建的子 agent）默认不继承 MCP 工具，需调用方显式传入；但子 agent 与主 agent **共享**预算累计和模型降级熔断状态，避免"创建子 agent"成为绕过硬预算上限的手段。
