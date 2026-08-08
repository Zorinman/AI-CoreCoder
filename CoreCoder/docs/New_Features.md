# New Features

记录在 `article/` 原教程之外，为本项目新增的扩展功能。每个功能对应
`docs/specs/` 下的一份 spec 和 `docs/adr/` 下的一份 ADR。

## 1. 并发安全分段调度（concurrency-safe segmented scheduling）

对应 spec：[docs/specs/0001-concurrency-scheduling.md](specs/0001-concurrency-scheduling.md)
对应 ADR：[docs/adr/0001-concurrency-safety-whitelist.md](adr/0001-concurrency-safety-whitelist.md)

### 问题

原实现里，`Agent` 收到多个 `tool_calls` 时只要数量大于 1 就统一丢进
`ThreadPoolExecutor` 并发执行，不区分工具是否有副作用。`bash`、
`edit_file`、`write_file` 这类会产生跨调用共享状态或副作用的工具，被并发调
用时可能出现结果覆盖、顺序错乱等竞态问题。

### 方案

- 给 `Tool` 基类增加类属性 `concurrency_safe: bool = False`（默认拒绝，
  未声明即视为不安全）。
- 只读、无共享可变状态的工具显式声明为 `True`：`read_file`、`glob`、
  `grep`。
- `bash`、`agent`（子 agent）保持默认（不安全），不引入命令前缀解析或白名
  单去细化 `bash` 的部分安全性。
- `Agent` 执行一批 `tool_calls` 时按原始顺序做"分段"：连续的安全调用聚合成
  一段并发执行，每个不安全调用单独成一段串行执行；段与段之间严格按原始出
  现顺序执行，保证不安全操作前后的因果顺序不被打乱。
- 返回结果列表顺序始终与原始 `tool_calls` 顺序一致，不因分段调度而改变。

### 代码变化

- `corecoder/tools/base.py`：`Tool` 基类新增 `concurrency_safe = False`。
- `corecoder/tools/read.py`、`glob_tool.py`、`grep.py`：声明
  `concurrency_safe = True`。
- `corecoder/agent.py`：新增 `_tool_is_safe()`、`_segment_tool_calls()`；
  改写 `_exec_tools_parallel()`，按分段结果决定每段用线程池并发还是直接串
  行执行。
- `tests/test_core.py`：新增分段调度相关的单元测试（分段结果、安全段确实
  并发重叠、不安全段严格无重叠、混合批次结果顺序保持一致）。

