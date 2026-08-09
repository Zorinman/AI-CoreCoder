# 01 - Agent 私有默认 Tool 与运行状态

**What to build**: 每个使用默认 Tool 的 Agent 都拥有独立的 Tool 实例、工作目录和变更记录；子 Agent 始终从创建它的 Agent 派生。CLI 通过 Agent 的只读快照显示当前 Agent 的变更文件。显式 `tools=[...]` 仍然定义完整 Tool 集合，旧的全局默认 Tool API 和相关说明被删除。

**Blocked by**: None - can start immediately.

**Status**: completed

- [x] 两个默认 Agent 的 Tool 和运行状态彼此隔离，创建后者不会改变前者的子 Agent 行为。
- [x] 同一 Agent 的工作目录和变更文件记录在多次对话之间持续。
- [x] 显式 Tool 列表不混入默认 Tool，CLI 只读取当前 Agent 的变更文件快照。
- [x] 旧全局默认 Tool API、测试和教学说明已删除或替换为 Agent 私有模型。
