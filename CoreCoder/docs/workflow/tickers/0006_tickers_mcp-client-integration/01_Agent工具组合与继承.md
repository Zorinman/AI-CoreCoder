# 01 — 建立 Agent 工具组合与继承 seam

**What to build**：让库调用者能够明确区分完整工具集合与附加工具，并保证子 Agent 继承外部能力时仍拥有独立的内置工具运行状态。

**Blocked by**：None — can start immediately

**Status**：ready-for-agent

- [ ] `tools=` 继续表示 Agent 的完整工具集合；未提供时使用默认工具，显式空集合能够构造无工具 Agent。
- [ ] 新增 `extra_tools=`，按确定顺序追加到默认工具或显式 `tools` 集合之后。
- [ ] 最终工具名称必须唯一；默认工具、附加工具或自定义完整集合发生重名时，Agent 在构造阶段返回明确错误。
- [ ] 工具 Schema、名称索引和系统提示始终从同一个已验证工具集合生成。
- [ ] 子 Agent 排除 `agent`，继承父 Agent 的附加工具，并重新创建自己的内置工具。
- [ ] 父子 Agent 的工作目录、变更文件集合和内置有状态工具实例互相隔离。
- [ ] 现有完整替换用例和新增附加工具用例均通过公开 Agent interface 验证。
