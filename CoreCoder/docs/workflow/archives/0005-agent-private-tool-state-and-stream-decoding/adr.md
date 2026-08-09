---
title: Agent 私有 Tool 状态与统一流式解码
status: archived
depends_on: []
implemented_in: working-tree
archived_on: 2026-08-10
---

# 归档记录

- **状态**：已归档。
- **实现范围**：Agent 私有默认 Tool 与运行状态、只读变更文件快照、统一流式响应解码，以及相关公开入口和 CLI 调用方更新。
- **实现位置**：`corecoder/agent.py`、`corecoder/cli.py`、`corecoder/llm.py`、`corecoder/tools/runtime.py`、`corecoder/tools/bash.py`、`corecoder/tools/edit.py` 与 `corecoder/tools/write.py`。
- **验证范围**：`tests/test_core.py`、`tests/test_tools.py` 与 `tests/test_stream_decoding.py`。

# 决策

CoreCoder 将默认 Tool 实例和 Agent 运行状态的生命周期绑定到单个 `Agent`，并删除共享的默认 Tool 注册表及其公开查询入口。显式传入的 Tool 集合继续完全替换默认集合；CLI 通过 Agent 的只读变更文件快照查询状态。所有 LLM 后端复用同一个受保护的流式解码器，以便文本、工具调用、用量和容错规则只定义一次。

## 备选方案

- 保留共享的默认 Tool 注册表并在构造时重置可变字段。该方案仍允许跨 Agent 共享可变实例，状态所有权不清晰。
- 保留 `ALL_TOOLS` 和 `get_tool` 作为兼容层。该方案保留了已废弃的全局所有权模型，违背本次不兼容、保持接口整洁的决定。
- 让各 LLM 后端继续独立解码流。该方案会持续复制协议规则，并要求每次变更同时维护多个实现。

## 后果

默认 Agent 的 Tool、工作目录和变更记录彼此隔离；不再支持导入旧的全局默认 Tool 注册表。流式响应的可观察行为保持不变，包括缺失字段容错、无效工具参数 JSON 回退、文本回调、按索引组装工具调用和用量统计。
