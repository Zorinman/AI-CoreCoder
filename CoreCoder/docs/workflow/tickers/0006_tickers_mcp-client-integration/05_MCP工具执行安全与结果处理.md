# 05 — 完善 MCP 工具执行安全与结果处理

**What to build**：让 MCP 工具调用在错误、敏感参数、大型输出和不支持内容出现时保持可预测，并向用户和模型提供足够但不过度暴露的信息。

**Blocked by**：02 — 打通单个 stdio MCP Server 的完整调用链

**Status**：ready-for-agent

- [ ] 所有 MCP 工具保持不可并发，Server annotations 不改变调度策略。
- [ ] MCP 工具调用失败后不自动重试，Server 失效后不自动重连。
- [ ] Server 工具执行错误作为可供模型修正的工具结果返回，协议、连接和超时错误具有明确分类与描述。
- [ ] 文本内容和结构化 JSON 能够转换为稳定的字符串工具结果。
- [ ] 图片、音频、ResourceLink、EmbeddedResource 和其他不支持内容返回明确的不支持错误，不进行 Base64 编码。
- [ ] 结果超过 15,000 字符时保留头尾并标明原始长度。
- [ ] 工具调用提示只显示限长的参数摘要。
- [ ] 任意嵌套层级中，字段名包含 `key`、`token`、`secret`、`password`、`auth` 或 `credential` 的值均被遮蔽。
- [ ] MCP Server 的外部费用不计入 LLM 硬预算，工具调用事实仍可被观察和测试。
- [ ] 参数直接交给 MCP Server 按其 `inputSchema` 处理，不新增客户端 JSON Schema 校验依赖。
