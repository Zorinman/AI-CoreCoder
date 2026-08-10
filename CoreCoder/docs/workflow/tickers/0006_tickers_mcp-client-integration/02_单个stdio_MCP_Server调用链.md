# 02 — 打通单个 stdio MCP Server 的完整调用链

**What to build**：让用户能够通过一份显式配置启动单个本地 stdio MCP Server，使其文本工具进入 Agent 工具集合、完成一次调用，并在 CoreCoder 退出时关闭连接和子进程。

**Blocked by**：01 — 建立 Agent 工具组合与继承 seam

**Status**：ready-for-agent

- [ ] 官方 MCP Python SDK 作为可选依赖使用，未启用 MCP 时基础 CoreCoder 不要求安装该依赖。
- [ ] 严格解析一个包含 `mcpServers` 的 stdio 配置，支持 `command`、`args` 和 `env`。
- [ ] stdio Server 使用直接进程执行而非 Shell，并继承 CoreCoder 的当前工作目录。
- [ ] 独立 MCP 生命周期所有者能够在后台异步运行 SDK，同时向同步 Agent 提供工具 adapter。
- [ ] Server 的 `inputSchema` 能够作为 Agent 工具参数 Schema 使用。
- [ ] Agent 能够调用一个 MCP 文本工具并把结果加入正常工具调用循环。
- [ ] 连接超时为 30 秒，工具调用超时为 120 秒。
- [ ] 正常退出能够关闭 MCP 会话并终止本 Ticket 启动的 Server 进程。
- [ ] 使用本地最小 stdio Server 验证从配置到工具结果的完整路径，不访问互联网。
