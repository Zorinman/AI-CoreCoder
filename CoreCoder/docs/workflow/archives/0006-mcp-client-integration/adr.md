---
title: MCP 客户端工具接入
status: archived
feature_id: 0006
implemented_in: working-tree
archived_on: 2026-08-10
---

# 归档记录

- **状态**：已归档。
- **实现范围**：用户级 MCP 配置、stdio 与 Streamable HTTP transport、MCP 工具快照、Agent 工具组合与继承、CLI 启动控制和 `/mcp` 状态命令。
- **实现位置**：`corecoder/mcp.py`、`corecoder/agent.py`、`corecoder/cli.py`、`corecoder/context.py` 与 `corecoder/tools/agent.py`。
- **验证范围**：`tests/test_mcp_runtime.py`、`tests/test_mcp_cli.py`、`tests/test_mcp_snapshot_context.py` 与 `tests/test_agent_tool_composition.py`。

# 以用户级配置和启动工具快照接入 MCP Client

CoreCoder 仅作为 MCP Client，通过官方 Python SDK v2 消费 stdio 或 Streamable HTTP Server 暴露的 Tools，不在本期提供 MCP Server 或其他 MCP 能力。MCP 连接由独立的 `McpRuntime` 统一拥有，以后台事件循环和 `ClientSessionGroup` 封装异步生命周期；`Agent` 继续保持同步工具调用循环，通过 `McpTool` adapter 使用远程工具。

MCP Server 默认来自用户拥有的 `~/.corecoder/mcp.json`，CLI 可用 `--mcp-config` 临时覆盖或用 `--no-mcp` 禁用。每个 Server 配置只能使用 `command`（stdio）或 `url`（Streamable HTTP）之一；`url` 接受用户显式声明的 HTTP 或 HTTPS 端点，本期不提供请求头或 OAuth。项目目录及其 `.env` 不得决定启动哪些 MCP Server，避免打开陌生工作区时隐式执行命令或连接端点。`tools=` 继续表示 Agent 的完整工具集合，新增 `extra_tools=` 表示在默认或指定集合上追加能力；子 Agent 重新创建自己的内置工具和运行状态，并继承父 Agent 的附加工具，包括 MCP 工具，但不继承 `agent`。

工具在启动时发现并形成固定快照。所有 MCP 工具均视为不可并发，不采信 Server annotations；收到工具列表变化通知时只提示用户重启，不动态修改 Agent 的工具索引、Schema 或系统提示。这个选择保留了 CoreCoder 当前同步、可读且可测试的核心循环，同时把外部协议、进程、超时、错误和关闭行为集中在一个明确的生命周期所有者中。

## 后果

- 支持 Tools、stdio 与 Streamable HTTP、文本与结构化 JSON 结果。
- Streamable HTTP 不支持自定义请求头、OAuth、旧 SSE 或自定义 transport；自动化测试使用本地 HTTP Server，不调用真实第三方服务。
- 重启 CoreCoder 是刷新工具列表和恢复失效连接的唯一方式。
- MCP 工具自身产生的外部费用不计入 CoreCoder 的 LLM 硬预算。
- 后续加入多模态结果、OAuth、旧 SSE 或动态工具刷新时，应作为独立功能重新设计，不得把相关复杂度泄漏进现有 `Agent` 接口。
