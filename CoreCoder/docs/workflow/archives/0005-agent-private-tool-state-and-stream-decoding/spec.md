---
title: Agent 私有 Tool 状态与统一流式解码
status: archived
depends_on: []
implemented_in: working-tree
archived_on: 2026-08-10
---

# Problem Statement

默认 Tool 在进程级共享可变实例。创建多个 Agent 时，子 Agent Tool 的父 Agent 引用会被后创建的实例覆盖，工作目录和已变更文件记录也会跨 Agent 泄漏。与此同时，不同 LLM 后端各自实现相同的流式协议解码规则，修复或扩展时容易产生行为分叉。

# Solution

每个未显式传入 Tool 集合的 Agent 都创建自己的一组默认 Tool，并拥有独立的 Agent 运行状态。显式传入的 Tool 集合保持为完整替换，不自动混入默认 Tool。已变更文件通过 Agent 的只读快照提供给 CLI。

所有后端把已获得的流交给同一个受保护的解码器。该解码器统一累计文本、工具调用和用量，调用文本回调，并保持既有的缺失字段和无效工具参数容错。后端仅负责请求参数、获得流和重试策略。

# User Stories

1. As a CoreCoder user, I want separately created Agents to keep independent Tool state, so that one Agent cannot affect another Agent's task execution.
2. As a CoreCoder user, I want each Agent to retain its working directory during its own conversation, so that sequential shell commands have predictable context.
3. As a CoreCoder user, I want the changed-file view to contain only files changed by my current Agent, so that a diff report is not polluted by another Agent.
4. As a CoreCoder user, I want child-agent execution to remain bound to the Agent that created it, so that work is not delegated through another Agent's model, messages, or tools.
5. As a library caller, I want `tools=[...]` to define the complete Tool set, so that explicitly selected tools are not supplemented with hidden defaults.
6. As a CoreCoder maintainer, I want the public API to omit the old global default Tool registry, so that ownership of mutable Tool instances is explicit.
7. As a CoreCoder user, I want OpenAI-compatible and LiteLLM backends to assemble equivalent streamed text, tools, and usage, so that backend choice does not alter response semantics.
8. As a CoreCoder user, I want text tokens to continue reaching the streaming callback in order, so that terminal output remains incremental.
9. As a CoreCoder user, I want fragmented tool calls to be assembled by call index, so that streamed function calls remain executable.
10. As a CoreCoder user, I want malformed tool argument JSON and optional provider fields to retain their current tolerant behavior, so that backend irregularities do not fail an entire response.

# Implementation Decisions

- Remove the shared global default Tool registry and its lookup API instead of preserving a compatibility layer.
- Construct fresh default Tool instances for every Agent that does not receive an explicit Tool list.
- Keep Agent runtime state private to the Agent instance and preserve it across that Agent's conversations.
- Expose the Agent's changed-file information as a read-only snapshot for CLI presentation.
- Treat an explicitly supplied Tool list as the complete Tool set.
- Retain parent-Agent wiring for an explicitly supplied child-Agent Tool, but scope it to that supplied Tool instance.
- Implement one protected stream decoder in the LLM abstraction. It consumes provider chunks and produces the response data used by all LLM backends.
- Keep request construction, stream acquisition, backend-specific parameters, and retry behavior in their respective backends.
- Preserve existing stream-decoding behavior for text callbacks, usage accumulation, tool-call ordering, JSON fallback, and missing optional fields.

# Testing Decisions

- Test externally observable behavior rather than the private factory, runtime-state object, or decoder implementation.
- Verify that independently created default Agents do not share Tool ownership, changed-file reporting, or child-Agent delegation state.
- Verify that a supplied Tool list remains the Agent's entire available Tool set.
- Verify the changed-file snapshot used by the CLI rather than reading internal Tool-module variables.
- Use the two LLM backends' public `chat()` behavior with deterministic fake streams to verify identical text, callback, usage, fragmented tool-call, empty-chunk, and malformed-JSON behavior.
- Reuse the existing fake-stream style used for LiteLLM tests; add the missing equivalent coverage for the standard LLM backend.

# Out of Scope

- Backward-compatible aliases for the old global Tool registry or lookup API.
- Changes to Tool scheduling, Tool schema format, or custom Tool implementation requirements.
- Changes to model fallback, retry, budget, or provider selection behavior.
- New provider-specific stream features beyond the current common response contract.

# Further Notes

The behavior change intentionally removes public global-default-Tool imports. Documentation and examples that describe those imports must be updated with the Agent-owned default Tool model.
