# 并行执行与子 agent

第一篇介绍主循环时，执行一个工具与执行多个工具采用不同路径。本篇先补全这一部分，再介绍让 agent 派生子 agent 的工具。这两项能力都在解决同一个问题：如何让 agent 处理多项工作，同时保持状态和结果不混乱。

## 一次回来好几个工具调用

模型并不总是一次只请求一个工具。例如要求它分别查看三个文件时，它很可能一次返回三个 `read_file` 调用。三个读取操作彼此独立，若串行执行只会增加等待时间，适合并发运行。

CoreCoder 在主循环里就是这么分流的：

```python
if len(resp.tool_calls) == 1:
    tc = resp.tool_calls[0]
    # ...直接执行...
else:
    # parallel execution for multiple tool calls
    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
```

单个调用直接执行；多个调用交给 `_exec_tools_parallel`。最新实现不会将整批调用直接提交给线程池，而是根据工具声明的并发安全性进行分段：

```python
def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
    for tc in tool_calls:
        if on_tool:
            on_tool(tc.name, tc.arguments)

    results = []
    for safe, group in self._segment_tool_calls(tool_calls):
        if safe and len(group) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(self._exec_tool, tc) for tc in group]
                results.extend(f.result() for f in futures)
        else:
            results.extend(self._exec_tool(tc) for tc in group)
    return results
```

`Tool.concurrency_safe` 默认是 `False`。`read_file`、`glob`、`grep` 这类只读工具显式声明为 `True`；`bash`、写文件、编辑文件和子 agent 保持默认。连续的安全调用组成一个并发段，安全段之间以及所有不安全调用仍严格按原顺序串行。结果列表也始终与原始 `tool_calls` 一一对应。三个相邻的文件读取本来要等三趟 IO，现在大致只等一趟；在它们前后的写入与命令则不会被抢跑。

这里使用线程而不是进程或协程，因为工具主要执行 I/O 密集型工作：读磁盘、等待子进程或网络响应。在这类场景中，Python 的 GIL 影响有限，而线程池又是标准库中最直接的并发原语，能够以较少代码满足需求。

## 这个简化，相对 Claude Code 差在哪

这里得把简化的边界讲清楚。Claude Code 的并发执行（公开拆解里叫 StreamingToolExecutor）比这激进得多，也精细得多。它有两点是 CoreCoder 没做的。

第一，Claude Code 可以边生成边执行。模型仍在输出后续内容时，已经完整的工具调用就能提前开始，而不必等待整段响应结束。CoreCoder 则等待本轮模型响应结束、拿到完整工具调用列表后再执行。它省去了这类投机执行的实现复杂度，代价是增加少量延迟。

二是 Claude Code 对调度和权限的细分仍多得多。CoreCoder 已经采用同样的默认拒绝原则：只读工具明确标记 `concurrency_safe=True`，未声明的工具一律串行。不过它仍不做流式投机执行、依赖分析、后台任务或 worktree 隔离；这里的分段调度只是一个清晰而保守的最小版本。

## 并行不是免费的：共享可变状态会咬人

并发执行最容易出事的地方，是多个并发任务共享一份可变状态。一旦不加防护，它们会互相踩。CoreCoder 把当前工作目录和本次会话改过的文件放进 `ToolRuntimeState`，由一个 `Agent` 的默认 `bash`、`write_file`、`edit_file` 共用；不同 agent 的默认工具和状态则各自新建。这样，两个彼此独立的 agent 不会因为 `cd` 或修改记录发生串扰。

但这不是让状态工具可以随便并发的理由。`bash` 会读写当前目录，`write_file` 与 `edit_file` 会改磁盘，`agent` 还会启动带同一模型链的子任务，它们都没有声明 `concurrency_safe`，于是调度器默认把它们留在串行段。这个默认拒绝比“先全并发、再给危险工具打补丁”更可靠：新工具作者若没有认真证明线程安全，遗漏声明的结果只是少一点并发，不会制造竞态。

## 子 agent：给自己开一个分身

`agent` 工具（`corecoder/tools/agent.py`，58 行）解决的是另一类问题。有些子任务工作量很大，例如「梳理陌生代码库中的认证实现」。如果由主 agent 直接完成，它需要读取大量文件、执行大量搜索，中间过程会占据主对话的上下文窗口，反而挤占真正任务所需的空间。

子 agent 的思路是：将这类重任务交给拥有独立上下文的子 agent，在自己的窗口中完成探索后，只向主 agent 返回精简结论。主 agent 的历史因此无需保留所有探索细节。

代码很直白：

```python
def execute(self, task: str) -> str:
    if self._parent_agent is None:
        return "Error: agent tool not initialized (no parent agent)"

    from ..agent import Agent

    parent = self._parent_agent
    sub = Agent(
        llm=parent.llm,
        tools=[t for t in parent.tools if t.name != "agent"],  # no recursive agents
        max_context_tokens=parent.context.max_tokens,
        max_rounds=20,
    )

    try:
        result = sub.chat(task)
        if len(result) > 5000:
            result = result[:4500] + "\n... (sub-agent output truncated)"
        return f"[Sub-agent completed]\n{result}"
    except Exception as e:
        return f"Sub-agent error: {e}"
```

主 agent 创建一个新的 `Agent`，两者共享同一个 LLM，但子 agent 拥有全新的 `messages` 列表，因此上下文彼此隔离。子 agent 会独立完成循环，轮次上限设为 20；若返回结果过长，还会截断到 5000 字符以内，避免精简后的结论再次占满主对话的窗口。

注意那行 `_parent_agent`。它是在主 `Agent` 的构造函数里接上的，就是第一篇提过的那个循环：

```python
for t in self.tools:
    if isinstance(t, AgentTool):
        t._parent_agent = self
```

构造 agent 时，会将每个 `AgentTool` 的父引用指向当前 agent，使工具在调用时能够取得对应的 LLM 和工具集。

## 为什么子 agent 不准生孙 agent

上面那行注释 `# no recursive agents` 是这段代码里最重要的一句。

派生子 agent 时，给它的工具集被刻意过滤掉了 `agent` 工具本身：`[t for t in parent.tools if t.name != "agent"]`。于是子 agent 手里没有「开分身」这个能力，它干不了的事只能自己硬扛，不能再往下派。

之所以禁止递归，是因为层层派生 agent 很容易失控：主 agent 派生子 agent，子 agent 又派生孙 agent，每一层都会消耗 token、占用线程并增加延迟。模型对任务是否还应继续拆分的判断并不可靠，可能不断细分而无法收束。限制为单层派生是一种保守的安全策略：子 agent 要么自行完成任务，要么失败并返回结果。

还记得第一篇强调过 `_tool_by_name` 是实例级的吗？正是因为每个 agent 认的工具是它自己的事，这里「把 agent 工具从子 agent 的集合里摘掉」才真正生效。子 agent 即便在某段历史里看到过 `agent` 这个名字，去调它也只会得到 `unknown tool 'agent'`。两个看起来不相干的设计，在这里咬合上了。测试 `test_agent_tool_scope_is_per_instance` 守的就是这条咬合。

## 和 Claude Code 的对照

Claude Code 的子 agent 系统（它的 AgentTool 公开拆解里超过一千行）要丰富得多：子 agent 有好几种运行模式，包括在独立的 git worktree 里跑、在后台异步跑；还内置了若干种预设的 agent 类型，各有各的系统提示和工具集。CoreCoder 把这一切收敛成最朴素的一种：同步派生、跑完返回、不准递归。

但「用独立上下文的子 agent 来隔离重活、保护主窗口」这个核心动机，两者是一样的。读懂 CoreCoder 这 58 行，你就抓住了多 agent 协作最本质的那一点：它首先是个上下文管理手段，其次才是个任务分解手段。很多人以为子 agent 是为了「并行干更多活」，其实它最大的价值是「把别的活的噪音挡在主对话之外」。

## 这一篇带走什么

- 模型一次返回多个独立工具调用时，用线程池并发跑是笔划算的买卖，IO 密集场景下线程池是最省事的并发原语。
- 并发不是默认能力。CoreCoder 将连续的 `concurrency_safe` 调用并发执行，将未声明安全的工具按原顺序串行，结果也保持原顺序。
- 默认工具状态归属于单个 agent；扩展工具前先问它是否无共享可变状态、是否能承受同时调用。不能明确证明时，不要声明 `concurrency_safe=True`。
- 子 agent 的首要价值是上下文隔离，让重活在独立窗口里折腾、只把精简结论交回主对话，其次才是任务分解。
- 子 agent 不准递归，是用「一刀切」换「绝不失控」。它靠的是实例级工具集这个机制落地。

下一篇，我们把这些零件装进一个真正能用的命令行工具：会话怎么存、断点怎么续、斜杠命令怎么接，以及一个藏在存盘里的安全细节。
