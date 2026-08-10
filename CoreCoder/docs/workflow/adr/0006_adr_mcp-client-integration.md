---
status: accepted
feature_id: 0006
---

# 以用户级配置和启动工具快照接入 MCP Client

CoreCoder 仅作为 MCP Client，通过官方 Python SDK v2 消费 stdio Server 暴露的 Tools，不在本期提供 MCP Server 或其他 MCP 能力。MCP 连接由独立的 `McpRuntime` 统一拥有，以后台事件循环和 `ClientSessionGroup` 封装异步生命周期；`Agent` 继续保持同步工具调用循环，通过 `McpTool` adapter 使用远程工具。

MCP Server 默认来自用户拥有的 `~/.corecoder/mcp.json`，CLI 可用 `--mcp-config` 临时覆盖或用 `--no-mcp` 禁用。项目目录及其 `.env` 不得决定启动哪些 MCP Server，避免打开陌生工作区时隐式执行命令。`tools=` 继续表示 Agent 的完整工具集合，新增 `extra_tools=` 表示在默认或指定集合上追加能力；子 Agent 重新创建自己的内置工具和运行状态，并继承父 Agent 的附加工具，包括 MCP 工具，但不继承 `agent`。

工具在启动时发现并形成固定快照。所有 MCP 工具均视为不可并发，不采信 Server annotations；收到工具列表变化通知时只提示用户重启，不动态修改 Agent 的工具索引、Schema 或系统提示。这个选择保留了 CoreCoder 当前同步、可读且可测试的核心循环，同时把外部协议、进程、超时、错误和关闭行为集中在一个明确的生命周期所有者中。

## 后果

- 第一版只支持 Tools、stdio、文本与结构化 JSON 结果。
- 重启 CoreCoder 是刷新工具列表和恢复失效连接的唯一方式。
- MCP 工具自身产生的外部费用不计入 CoreCoder 的 LLM 硬预算。
- 后续加入 Streamable HTTP、多模态结果或动态工具刷新时，应作为独立功能重新设计，不得把相关复杂度泄漏进现有 `Agent` 接口。
