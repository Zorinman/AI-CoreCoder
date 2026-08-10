# MCP 配置与使用

## 支持范围

- 仅支持 MCP Tools。
- 本地 Server 使用 stdio transport。
- 远程 Server 使用 Streamable HTTP transport。
- 不支持 Resources、Prompts、旧 SSE、自定义请求头、OAuth、自动重试和自动重连。

## 安装与配置位置

安装可选依赖：

```bash
pip install "corecoder[mcp]"
```

默认配置文件为 `~/.corecoder/mcp.json`。也可以通过 `--mcp-config PATH` 为单次启动指定配置文件。

MCP 配置只能由用户显式提供；项目目录、项目 `.env` 和已保存会话不会加载或决定 MCP Server。

## 配置格式

根对象只能包含 `mcpServers`。每个 Server 必须选择以下一种传输，不能混用字段：

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

| 传输 | 必填字段 | 可选字段 | 说明 |
| --- | --- | --- | --- |
| stdio | `command` | `args`、`env` | 直接启动可执行文件，不经过 Shell。`env` 支持 `${NAME}` 环境变量引用。 |
| Streamable HTTP | `url` | 无 | `url` 必须是绝对 `http://` 或 `https://` 地址。当前不支持请求头和 OAuth。 |

## 启动与调用

使用默认配置：

```bash
corecoder
```

使用指定配置或禁用 MCP：

```bash
corecoder --mcp-config ./mcp-firecrawl.json
corecoder --no-mcp
```

CoreCoder 在构造 Agent 前连接 Server、发现工具并自动将其加入 Agent。模型可见工具名为 `mcp__<server-key>__<tool-name>`；例如 Firecrawl 的搜索工具为 `mcp__firecrawl__firecrawl_search`。

交互模式中输入 `/mcp` 可查看当前已连接的 MCP Server 及其可用工具数量，不显示工具名称。

一次性模式可通过提示词要求 Agent 调用工具：

```bash
corecoder --mcp-config ./mcp-firecrawl.json -p "使用 firecrawl 搜索 MCP 工具查找 Go 官方文档。"
```

## 运行行为与限制

- 工具在启动时形成固定快照；Server 通知工具列表变化后，重启 CoreCoder 才会刷新。
- 多个 Server 的同名工具通过 Server 配置键隔离；所有 MCP 工具串行执行。
- 连接超时为 30 秒，单次工具调用超时为 120 秒。
- MCP 返回文本和结构化 JSON；其他内容类型返回错误，超过 15,000 字符的结果会截断。
- MCP 工具调用可能产生第三方服务费用，不受 CoreCoder 的 LLM 硬预算限制。
