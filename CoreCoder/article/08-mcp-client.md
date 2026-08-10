# MCP 客户端：让 agent 受控地接入外部工具

CoreCoder 原有的文件、Shell、搜索和子 agent 工具足以完成许多本地编码任务，但它们不应承担所有外部能力。网页检索、企业服务、数据库或团队内部系统通常已有独立的工具服务。MCP（Model Context Protocol）提供了一套让 agent 发现和调用这些服务的通用协议。

本篇讨论的重点不是“让模型能调更多工具”，而是如何把这件事接入得可控：谁有权决定连接哪些服务、异步协议如何放入同步 agent、外部工具变化时怎样保持运行时一致，以及哪些风险仍必须由使用者承担。

## CoreCoder 是 MCP Client，不是万能代理

MCP 中至少有三个角色：Client 发起连接和调用，Server 提供能力，Host 将 Client 放进具体应用。CoreCoder 作为 Host 内的 MCP Client，只消费 Server 提供的 Tools，并将它们适配为项目自己的 `Tool` 接口。

这项范围刻意很小：不加载 Resources 或 Prompts，也不在项目中充当 MCP Server。对一个编码 agent 而言，模型已经拥有自己的系统提示词、上下文和对话管理；最需要补充的是可调用的外部动作。先把 Tools 的生命周期、命名和异常处理做好，比同时纳入更多协议对象更重要。

## 配置文件就是连接授权

外部工具不应由仓库内容、`.env` 或会话历史悄悄决定。一个会话可能来自别人，一个项目也可能带有不可信配置；如果它们可以自动启动本地进程或连接远程服务，恢复会话就会变成隐式授权。

因此 CoreCoder 只读取用户显式提供的配置：默认位置是 `~/.corecoder/mcp.json`，也可以在本次启动时用 `--mcp-config PATH` 覆盖。配置根对象只包含 `mcpServers`：

```json
{
  "mcpServers": {
    "local-tools": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": {"API_TOKEN": "${MY_MCP_TOKEN}"}
    },
    "firecrawl": {
      "url": "https://mcp.firecrawl.dev/v2/mcp"
    }
  }
}
```

`local-tools` 是本地 stdio Server：CoreCoder 直接启动 `command`，并传入参数和展开后的环境变量，不经过 Shell。`firecrawl` 是远程 Streamable HTTP Server：`url` 必须是绝对 `http://` 或 `https://` 地址。两套字段互斥，一个 Server 不能同时既是子进程又是远程端点。

这种严格配置看似不如“随便多写几个字段”方便，但它消除了传输歧义，也使实现边界清晰。当前没有自定义请求头、OAuth、旧 SSE、自动重试或自动重连；需要这些能力时，应先明确认证和失败策略，再扩展协议层。

## 把异步 MCP 放进同步 Tool 接口

Agent 的工具调用接口是同步的：模型返回工具名和参数，Agent 调用 `Tool.execute()`，再把字符串结果写回消息历史。MCP Python SDK 则使用异步会话。若让 Agent 直接处理事件循环，CLI、测试和工具调度都会被协议细节侵入。

`McpRuntime` 因而成为一条明确的边界：它在后台线程中维护独立的 `asyncio` 事件循环、MCP 会话、stdio 子进程和资源退出栈。启动时连接每个 Server 并调用工具发现；调用时把同步请求提交给该事件循环；退出时统一关闭会话和进程。

```text
CLI
-> 读取用户 MCP 配置
-> McpRuntime.start()
-> 连接 Server、发现工具
-> McpTool[]
-> Agent(extra_tools=McpTool[])
-> McpTool.execute()
-> McpRuntime.call_tool()
```

`McpTool` 是普通 `Tool` 的适配器。它保存远程工具的描述和输入 Schema，并将 `execute()` 接收的命名参数转交给运行时。这样 Agent 无须知道 stdio、HTTP、会话对象或协程；MCP SDK 也不需要知道 Agent 的消息格式。

## 名称空间与启动快照

多个 Server 可能同时提供名为 `search` 的工具。直接把名称放进 Agent 会导致冲突，也无法让模型知道调用会去往何处。CoreCoder 将每个发现的工具命名为：

```text
mcp__<server-key>__<tool-name>
```

例如 Firecrawl 的搜索工具可以显示为 `mcp__firecrawl__firecrawl_search`。前缀同时表达协议来源、Server 身份和远程工具身份，因此名称既可读又唯一。

工具列表在启动时形成快照。Server 后续通知“工具新增或删除”时，正在运行的 Agent 仍使用同一批 `McpTool`，需要重启 CoreCoder 才会重新发现。这个选择牺牲了热更新，却让模型可见的工具 Schema、工具调度和会话行为在一次运行中保持一致。外部工具被声明为 `concurrency_safe = False`，因此 Agent 串行执行它们，避免共享会话或外部副作用发生并发竞态。

## 失败应该被隔离，但不能被掩盖

启动时，CoreCoder 为每个 Server 产生一个 `McpStartupReport`，其中包含连接状态、工具数量和安全的错误摘要。一个 Server 失败不会阻止其他已配置 Server 注册；当没有任何可用工具时，启动才失败。

连接等待上限为 30 秒，单次工具调用上限为 120 秒。调用结果接受文本和结构化 JSON，其他内容类型返回错误；过长结果会截断，防止单个外部响应挤占模型上下文。运行时不会自行重试或重连：这类策略可能在失败时重复执行有副作用的操作，必须由未来的明确需求来约束。

MCP 失败与模型失败也应分开看待。LLM 硬预算只统计模型成本，不能覆盖网页检索、数据库调用或 SaaS API 的第三方费用。接入一个 MCP Server 前，需要理解它的权限、计费方式、数据流向和失败副作用。

## 启动、观察与调用

先安装可选依赖：

```bash
pip install "corecoder[mcp]"
```

配置完成后直接启动：

```bash
corecoder
```

也可以对单次运行使用另一份配置，或完全跳过 MCP：

```bash
corecoder --mcp-config ./mcp-firecrawl.json
corecoder --no-mcp
```

启动输出会说明每个 Server 是否连接成功。进入交互模式后，输入 `/mcp` 可查看当前已连接的 Server 和各自的工具数量；它不列出工具名称，因此状态检查不会无谓扩大终端输出。模型在需要时自行选择已经注册的 `mcp__...` 工具，使用者也可以在提示词中指定目标，例如“使用 Firecrawl 搜索 Go 官方文档”。

MCP 为 CoreCoder 带来了可扩展的工具生态，但它没有取消边界：连接权仍在用户，工具集仍在启动时固定，外部副作用和费用仍需要独立评估。把这些界限放在接口设计中，agent 才能在能力增长后继续保持可理解和可控。
