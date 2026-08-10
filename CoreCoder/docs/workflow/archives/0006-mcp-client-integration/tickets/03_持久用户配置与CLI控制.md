# 03 — 提供持久用户配置与 CLI 控制

**What to build**：让用户配置一次用户级 MCP Server 集合后，在新对话、重启和会话恢复时自动连接，同时能够按次覆盖或禁用 MCP。

**Blocked by**：02 — 打通单个 stdio MCP Server 的完整调用链

**Status**：completed

- [ ] CoreCoder 默认从用户级 `~/.corecoder/mcp.json` 加载 MCP Server 配置。
- [ ] `--mcp-config` 能够为本次启动覆盖默认配置，`--no-mcp` 能够禁用本次启动的 MCP。
- [ ] 同时提供覆盖和禁用选项时，CLI 在启动阶段返回明确的互斥参数错误。
- [ ] 项目目录和项目 `.env` 不能决定启动哪些 MCP Server。
- [ ] 新对话、重启和使用 `-r` 恢复会话时均遵循相同的用户级配置解析规则。
- [ ] 会话文件不保存 MCP 配置路径、Server 定义或凭据。
- [ ] 历史包含 MCP 调用但本次禁用 MCP 时，CLI 提示当前不会提供 MCP 工具且不自动恢复连接。
- [ ] `corecoder --help` 和 REPL `/help` 均展示 MCP 覆盖与禁用选项，并标明它们是启动参数。
- [ ] 使用 MCP 但缺少可选依赖时，CLI 退出并给出安装 `corecoder[mcp]` 的准确提示；未使用 MCP 时不检查该依赖。
