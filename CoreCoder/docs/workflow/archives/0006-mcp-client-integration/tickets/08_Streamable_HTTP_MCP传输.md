# 08 — 支持 Streamable HTTP MCP 传输

**What to build**：让用户能在现有 `mcpServers` 配置中以 `url` 接入远程 Streamable HTTP Server，并复用既有工具发现、命名、调用、快照和生命周期管理。

**Blocked by**：02 — 打通单个 stdio MCP Server 的完整调用链；03 — 提供持久用户配置与 CLI 控制；04 — 支持多 Server 聚合与可靠注册；07 — 完成生命周期、集成验证与用户文档

**Status**：completed

- [x] Server 配置在 `command`/`args`/`env` 的 stdio 定义与仅含 `url` 的 Streamable HTTP 定义之间严格二选一。
- [x] `url` 仅接受有效的 `http://` 或 `https://` 端点；不支持请求头、OAuth 或其他 HTTP 客户端自定义。
- [x] `McpRuntime` 根据配置使用 `stdio_client` 或 `streamable_http_client`，并让两种 transport 共用工具发现、调用、超时、命名空间、通知和关闭逻辑。
- [x] 关闭 Runtime 时清理 stdio 子进程并正常关闭 Streamable HTTP 会话。
- [x] 自动化测试覆盖 URL 配置、无效的混合配置，以及本地 Streamable HTTP Server 的工具发现和调用；测试不访问互联网。
- [x] README 与中文 README 说明两种配置形式、传输边界和 Streamable HTTP 的无鉴权限制。
