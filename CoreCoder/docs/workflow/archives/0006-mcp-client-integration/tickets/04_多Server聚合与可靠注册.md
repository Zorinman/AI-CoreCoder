# 04 — 支持多 Server 聚合与可靠注册

**What to build**：让用户配置的多个 MCP Server 能够同时提供唯一、可验证的工具，并在部分外部依赖失败时继续使用其余正常 Server。

**Blocked by**：02 — 打通单个 stdio MCP Server 的完整调用链

**Status**：completed

- [ ] 一个 MCP 生命周期所有者通过单个多会话聚合能力管理全部配置 Server。
- [ ] 每个模型可见工具名使用 `mcp__<server配置键>__<工具原名>` 形成稳定命名空间。
- [ ] 工具名只允许 ASCII 字母、数字、下划线和连字符，且不超过 64 字符。
- [ ] 工具名称规范化后发生冲突、Schema 无效或名称超限时，对应 Server 注册失败，禁止静默覆盖。
- [ ] 单个 Server 连接、初始化或注册失败时，其他正常 Server 仍能完成启动并提供工具。
- [ ] 零工具 Server 显示警告；全部 Server 失败或最终没有可用 MCP 工具时，CoreCoder 启动失败。
- [ ] 启动摘要按 Server 显示配置键、连接结果、错误摘要和工具数量。
- [ ] 启动摘要不显示 Server 命令、参数、环境变量或凭据。
- [ ] 子 Agent 能够调用继承的 MCP 工具，且共享同一个 MCP 生命周期所有者而不重复启动 Server。
