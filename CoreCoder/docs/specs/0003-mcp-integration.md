---
title: 接入 Model Context Protocol（MCP）工具生态
status: ready-for-agent
depends_on: [0001-concurrency-scheduling, 0002-fallback-chain-and-budget]
---

# Problem Statement

CoreCoder 目前只有 7 个内置工具（`bash`、`read_file`、`write_file`、
`edit_file`、`glob`、`grep`、`agent`），要用上任何外部能力（数据库查询、第
三方 API、其他现成的 MCP server 提供的工具）都需要手写新的 `Tool` 子类。
Model Context Protocol 已经有大量现成的第三方 server 实现，如果 Agent 能
直接接入这个生态，就不需要每个外部能力都手写一遍。

# Solution

CoreCoder 只作为 MCP 的客户端（不提供 MCP server 侧能力）。连接和发现远程
MCP 工具的逻辑放在 CLI 入口层，不放进 `Agent` 类内部——这样当 `Agent` 被当
作库以编程方式使用（不经过 CLI）时，构造一个 Agent 不会有任何意外的外部进
程被启动或网络连接被建立，行为保持可预测。

CLI 启动时按固定文件名 `.mcp.json`（`mcpServers` 格式，与 Claude Code 自身
使用的格式一致）发现要连接的 MCP server 列表，连接后拉取每个 server 提供的
工具列表，将每个远程工具包装成一个 `Tool` 子类实例注册进 Agent 可用的工具
集合。远程工具的 `inputSchema` 本身就是标准 JSON Schema，可以直接复用为
`Tool.parameters`，不需要额外转换。

为避免多个 MCP server 之间，或 MCP 工具与内置工具之间发生工具名冲突，所有
从 MCP 拉取的工具在注册时统一加上双下划线分隔的前缀（server 名 + 工具原
名）。

所有 MCP 工具一律视为并发不安全（`concurrency_safe` 不声明，即保持默认
`False`），不采信 MCP 协议里 server 自行上报的 `readOnlyHint` 等标注——这
些标注在协议规范中被明确说明只是未经验证的提示，不是保证，不能作为并发调
度依据。

MCP SDK 是异步（`async`）接口，而 CoreCoder 现有的工具执行接口
（`Tool.execute()`）和线程池并发方案都是同步的。为了不改变现有同步接口，
每个 MCP server 连接对应一个独立的、贯穿该连接生命周期的 asyncio 事件循环
线程，`Tool.execute()` 内部通过线程安全的方式把调用转发到这个事件循环上执
行并等待结果，对上层调用者仍然表现为一次普通的同步方法调用。

子 agent 默认不会自动继承父 Agent 已连接的 MCP 工具；如果调用者希望某个子
agent 也能使用某些 MCP 工具，需要在触发子 agent 时显式传入。但子 agent 仍
然与父 Agent 共享同一份预算/熔断状态（[0002-fallback-chain-and-budget](0002-fallback-chain-and-budget.md)
中确立的规则），只是不共享 MCP 工具集合本身。

# User Stories

1. 作为用户，我希望在项目根目录放一个 `.mcp.json`，声明要连接的 MCP
   server，CoreCoder 启动时自动连接并让这些 server 提供的工具在对话中可用。
2. 作为用户，我希望 `.mcp.json` 的格式和字段含义与 Claude Code 已经在用的
   `mcpServers` 格式一致，这样我已有的 MCP server 配置可以直接复用。
3. 作为用户，当某个 MCP server 提供的工具名和内置工具名（或另一个 MCP
   server 的工具名）冲突时，我希望它们仍然都能正常被区分和调用，不会互相覆
   盖。
4. 作为把 `Agent` 当库以编程方式使用的开发者（不经过 CLI），我希望仅仅构
   造一个 `Agent` 实例不会触发任何 MCP server 的连接或子进程启动——所有 MCP
   相关的连接行为都应该是我在 CLI 层显式触发的,不是 `Agent.__init__` 的隐
   藏副作用。
5. 作为用户，当模型在同一轮请求调用一个或多个 MCP 工具时，我希望它们被当
   作并发不安全处理（不与其他调用并发执行），即使该 MCP server 自称这个工
   具是只读的。
6. 作为用户，当我用 `agent` 工具触发一个子任务时，我希望这个子 agent 默认
   看不到父 Agent 已连接的 MCP 工具，除非我在触发子 agent 时明确把这些工具
   传给它。
7. 作为用户，当子 agent 确实消耗了预算（无论是否使用了 MCP 工具），我希望
   这部分花费仍然计入父 Agent 的总预算和当前生效的降级状态，与
   [0002-fallback-chain-and-budget](0002-fallback-chain-and-budget.md) 中
   的规则一致。
8. 作为 CoreCoder 维护者，我希望 MCP 工具的 `inputSchema` 可以直接当作
   `Tool.parameters` 使用，不需要额外的字段转换或校验层。
9. 作为 CoreCoder 维护者，我希望 MCP 相关的异步调用不会要求把现有同步的
   `Tool.execute()` 接口或现有的线程池并发调度方案改成异步,MCP 的异步细节
   应该被封装在 MCP 工具自身内部。
10. 作为用户，当某个 MCP server 连接失败或工具调用失败时，我希望得到一个
    清晰的错误信息（与内置工具执行失败时的错误处理方式一致），而不是让整个
    CLI 进程崩溃。

# Implementation Decisions

- CoreCoder 仅实现 MCP 客户端能力，不实现 MCP server 侧。
- MCP server 的发现、连接建立、工具列表拉取等逻辑放在 CLI 入口层，不放进
  `Agent` 类的构造或方法内部；`Agent` 只负责持有已经构造好的工具实例（与其
  处理内置工具的方式一致）。
- 配置文件固定名为 `.mcp.json`，结构复用 `mcpServers`（键为 server 名，值
  为该 server 的连接参数，如启动命令/参数/环境变量），与 Claude Code 自身
  的配置格式保持一致，不自行发明新格式。
- 每个从 MCP 拉取的远程工具包装为一个 `Tool` 子类实例；其 `parameters` 直
  接使用远程工具上报的 `inputSchema`（标准 JSON Schema），不做额外转换。
- 注册到 Agent 工具集合时，工具名统一重命名为"server 名 + 双下划线 + 工具
  原名"的形式（用双下划线分隔，避免与包含单下划线的原始名称产生歧义），确
  保跨 server、跨内置工具的唯一性。
- 所有 MCP 工具不声明 `concurrency_safe`（即保持基类默认 `False`），不读
  取、不信任 MCP 协议里的 `readOnlyHint`/`destructiveHint`/`idempotentHint`
  等自报标注来决定并发调度——协议规范本身把这些标注定义为未经验证的提示。
- 每个 MCP server 连接对应一个独立的、长期运行的 asyncio 事件循环线程；对
  应的 `Tool.execute()` 实现内部把调用转发到该线程的事件循环上执行并阻塞等
  待结果返回，对调用者呈现为普通同步方法。这个转发机制封装在 MCP 工具类内
  部，不改变 `Tool` 基类接口，也不改变 Agent 现有的
  `ThreadPoolExecutor`([0001-concurrency-scheduling](0001-concurrency-scheduling.md)
  中的分段调度)方案。
- 子 agent 工具触发子 Agent 时，默认不会把父 Agent 已连接的 MCP 工具传入子
  Agent 的工具集合；如需传入，由调用方显式指定。子 agent 仍然共享父 Agent
  的降级/熔断/预算累计状态（沿用 [0002-fallback-chain-and-budget](0002-fallback-chain-and-budget.md)
  的规则），这一点不因是否涉及 MCP 而改变。

# Testing Decisions

复用现有测试套件的风格，参考 `test_litellm.py` 中对 `litellm` 模块用
`sys.modules` 做假注入、再直接构造类实例调用方法断言的手法：

- 对 `mcp` SDK 做同样的假注入（构造一个假的 `ModuleType`，提供假的
  client/`list_tools`/`call_tool` 行为），不启动真实子进程或真实网络连接。
- 直接测试"远程工具列表 → 包装为 `Tool` 实例列表"的转换逻辑：断言生成的
  `Tool` 实例的 `name` 带有正确的 server 前缀、`parameters` 与原始
  `inputSchema` 一致、且默认未声明 `concurrency_safe`（即为 `False`）。
- 直接测试工具名冲突场景：构造两个不同 server 但提供同名原始工具的假配置，
  断言包装后的最终工具名不冲突。
- 直接测试"MCP 工具执行转发到事件循环线程"这一层：用假的 client 模拟一次
  工具调用，断言 `execute()` 同步返回了假 client 异步方法产生的结果，不需
  要真实起一个 asyncio loop 之外的可观察行为。
- 子 agent 不继承 MCP 工具：直接构造一个持有假 MCP 工具的父 Agent，触发一
  次子 agent 调用（不显式传入 MCP 工具），断言生成的子 Agent 的工具集合中
  不包含该 MCP 工具；再测试显式传入的情形，断言子 Agent 工具集合中包含它。

不引入新的测试框架，不做真实的 MCP server 子进程集成测试；真实 server 的
端到端连通性验证留给人工/手动验证，不计入本 feature 的自动化测试范围。

# Out of Scope

- 不实现 MCP server 侧（CoreCoder 自己作为 server 被其他客户端连接）。
- 不基于 MCP 协议自报的工具标注（`readOnlyHint` 等）做任何并发调度或安全
  性判断。
- 不实现"用户可配置某个 MCP 工具的并发安全性"的覆盖机制。
- 不改变现有 `Tool` 基类接口，不把 `Tool.execute()` 改为异步接口。
- 不做真实第三方 MCP server 的端到端连通性自动化测试。

# Further Notes

本 feature 是三份渐进 spec 中的最后一份，同时依赖前两份建立的规则：MCP 工
具复用 [0001-concurrency-scheduling](0001-concurrency-scheduling.md) 确立
的"默认不并发"惯例（不因协议自报的 hint 而改变默认值），子 agent 与 MCP
工具的关系（不自动继承）与子 agent 与预算状态的关系（默认共享，见
[0002-fallback-chain-and-budget](0002-fallback-chain-and-budget.md)）是两
条独立但都需要遵守的规则,不要把两者混淆成"子 agent 完全隔离"或"子 agent 完
全共享"的单一简单规则。对应决策记录见
`docs/adr/0003-mcp-client-only-untrusted-hints.md`，项目词汇见
`CONTEXT.md` 中的"MCP 工具前缀"条目。
