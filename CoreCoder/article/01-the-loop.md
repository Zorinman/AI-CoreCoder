# 一个 agent 的本体，是一个 while 循环

如果只能用一句话解释编码 agent，我会这样说：它是一个循环，反复询问模型「下一步做什么」，执行模型选择的操作，再把结果反馈给模型，直到模型不再请求工具并给出答案。

这个说法听起来朴素，事实却正是如此。Claude Code 的整体实现已有几十万行，但其公开拆解中的核心 `query.ts`，主体仍是一个约一千七百行的 `while` 循环。CoreCoder 将同样的循环收在 `corecoder/agent.py` 中，连同空行和注释共 200 行。两者的结构相似，区别在于后者可以一口气读完。

这一篇，我们就把这个循环逐段读透。

## 先看骨架

打开 `agent.py`，核心是 `Agent.chat()` 这个方法。下面保留与主循环直接相关的当前骨架；工具执行、并发分段和中断回填留给后文展开：

```python
def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
    """Process one user message. May involve multiple LLM/tool rounds."""
    self.messages.append({"role": "user", "content": user_input})
    self.context.maybe_compress(self.messages, self.llm)
    fallback_messages = []

    for _ in range(self.max_rounds):
        try:
            if hasattr(self.llm, "can_start_request") and not self.llm.can_start_request():
                return "Estimated cost exceeds the hard budget ..."
            previous_index = getattr(self.llm, "active_index", 0)
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )
            if getattr(self.llm, "active_index", 0) != previous_index:
                message = f"当前使用模型已切换为备用模型 {self.llm.model}。"
                self.messages.append({"role": "system", "content": message})
                fallback_messages.append(message)
        except BudgetExceededError as exc:
            return str(exc)

        # no tool calls -> LLM is done, return text
        if not resp.tool_calls:
            self.messages.append(resp.message)
            return "\n\n".join([*fallback_messages, resp.content])

        # tool calls -> execute
        self.messages.append(resp.message)
        # ...执行工具，把结果 append 回 self.messages...
        self.context.maybe_compress(self.messages, self.llm)

    return "(reached maximum tool-call rounds)"
```

理解这个骨架，就掌握了 agent 的大半工作流程。它依次完成以下事情：

它先将用户输入追加到对话历史，并进行一次压缩检查（只有窗口接近上限时才真正压缩，详见[第四篇](04-context.md)）。随后进入循环：每一轮都将完整历史和全部工具 schema 发送给模型。模型返回后只有两种情况：如果返回纯文本而没有工具调用，说明它认为任务已经完成，循环随即结束；如果返回若干工具调用，就逐个执行，并将每个结果作为 `tool` 消息写回历史，然后进入下一轮。

循环持续进行，直到模型不再请求工具。

这里有一个值得停下来的细节：何时结束由模型自己判断，没有外部规则直接判定「任务是否完成」。模型拿到目标和工具后，可能读几个文件、运行一次测试，然后认为信息已经足够并给出回答。这种「由模型判断收敛」的设计是各类 agent 的共同前提，也是它有时过早结束、有时过度操作的原因。理解这一点，就能明白 agent 的行为为何不像传统 `if-else` 那样完全可预测：你是在与会自行判断下一步的模型协作。

## 为什么是 for，不是 while(true)

Claude Code 的循环写成 `while(true)`，靠内部的预算和错误恢复来退出。CoreCoder 这里写成 `for _ in range(self.max_rounds)`，`max_rounds` 默认 50。

这不是单纯的风格差异，而是一道明确的刹车。模型可能陷入无法自行退出的循环：读文件、发现问题、继续读取，依然没有进展。没有上限，它会持续消耗 token，直到用户手动按下 Ctrl+C。50 轮是一个经验值，常规任务通常达不到；一旦触及上限，循环会返回 `(reached maximum tool-call rounds)`，将控制权交还给用户。

任何要把 LLM 放进循环的人，第一件事就该是给循环一个硬上限。这是最便宜的保险。

## 工具结果长什么样

模型要工具，我们就得把执行结果以它认得的格式喂回去。OpenAI 的 function calling 协议规定，一个 `assistant` 消息如果带了 `tool_calls`，那么后面必须跟上数量相等、`tool_call_id` 一一对应的 `tool` 消息。CoreCoder 老老实实照办：

```python
result = self._exec_tool(tc)
self.messages.append({
    "role": "tool",
    "tool_call_id": tc.id,
    "content": result,
})
```

注意每条 tool 结果都带着它对应的 `tc.id`。这个 id 配对关系，是后面好几个坑的根源。记住它，第四篇讲上下文压缩、本篇下面讲中断时，都会回到这个约束上来。

## 一个工具的执行，怎么区分「参数错」和「工具自己炸了」

`_exec_tool` 短短十几行，但藏着一个我很喜欢的工程判断：

```python
def _exec_tool(self, tc) -> str:
    tool = self._tool_by_name.get(tc.name)
    if tool is None:
        return f"Error: unknown tool '{tc.name}'"
    # validate arguments first so a TypeError raised *inside* the tool isn't
    # mislabelled as a bad-arguments error from the caller
    try:
        inspect.signature(tool.execute).bind(**tc.arguments)
    except TypeError as e:
        return f"Error: bad arguments for {tc.name}: {e}"
    try:
        return tool.execute(**tc.arguments)
    except Exception as e:
        return f"Error executing {tc.name}: {e}"
```

为什么要先用 `inspect.signature().bind()` 试一次参数，再真正调用？因为如果直接 `tool.execute(**tc.arguments)`，一旦抛出 `TypeError`，你根本分不清是模型给的参数对不上函数签名（模型的错，该让它改），还是工具内部某行代码自己抛了 `TypeError`（工具的 bug，跟参数没关系）。这两种情况返回给模型的提示应该完全不同：前者要告诉它「你参数填错了」，后者要告诉它「工具执行出错了」。

先 `bind` 一遍，就把「参数能不能对上签名」这件事单独拎出来判断了。`bind` 不会真的执行函数，只检查实参能否合法绑定到形参。绑不上，是参数问题；绑得上之后再调用时炸的，那是工具内部的问题。这个区分写进了测试 `test_exec_tool_distinguishes_bad_args_from_internal_error`，一个内部抛 `TypeError` 的工具，不会被误报成参数错误。

这种细节，是「能跑的 demo」和「不坑人的工具」之间的距离。模型拿到一句精确的错误反馈，下一轮就能自我修正；拿到一句误导的反馈，它会朝错误的方向越改越远。

## 每个 agent 只认自己那套工具

注意上面用的是 `self._tool_by_name`，它在构造函数里建好：

```python
self._tool_by_name = {t.name: t for t in self.tools}
```

是一个实例级的字典，不是全局表。这件事在主 agent 上看不出差别，但在[子 agent](05-parallel-and-subagents.md) 上很关键：主 agent 派生子 agent 时，会把工具集裁掉一部分（比如不让子 agent 再去开孙子 agent）。如果工具查找走的是全局表，这个裁剪就形同虚设，子 agent 还是能叫出被禁的工具。实例级字典保证了「这个 agent 能用哪些工具」是它自己的事，谁也越不过去。测试 `test_agent_tool_scope_is_per_instance` 盯的就是这个：一个只给了 `read_file` 的 agent，去叫 `bash` 会得到 `unknown tool 'bash'`，哪怕 `bash` 是个真实注册过的工具。

## 模型被打断的那一瞬间

真实使用里，用户随时会按 Ctrl+C。麻烦在于，Ctrl+C 可能正好打在「模型已经返回了一批工具调用、但工具还没全部跑完」的中间。这时候对话历史里有一条带 `tool_calls` 的 assistant 消息，却缺了部分对应的 `tool` 回复。下一次请求带着这段残缺历史发出去，OpenAI 兼容的 API 会直接拒绝，因为它违反了「每个 tool_call 必须有配对回复」的协议。一次中断，就把整个会话搞脏了。

CoreCoder 的处理是在循环里专门接住这个异常：

```python
except KeyboardInterrupt:
    # Ctrl+C mid-execution would leave the assistant tool_calls
    # message without replies, poisoning the next request; backfill
    self._answer_pending_tool_calls(resp.tool_calls)
    raise
```

`_answer_pending_tool_calls` 做的事很简单，给每个还没拿到回复的工具调用补一条 `[interrupted]` 占位回复，让历史重新合法，然后再把异常抛上去交给上层处理：

```python
def _answer_pending_tool_calls(self, tool_calls):
    answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
    for tc in tool_calls:
        if tc.id not in answered:
            self.messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": "[interrupted]",
            })
```

先收集已经回复过的 id，只给缺的那些补占位，已经跑完的工具结果不会被覆盖（测试 `test_interrupt_backfills_missing_tool_replies` 专门验证了这点：已答复的调用不会被重复填）。这样用户中断之后还能接着聊，历史是干净的。

这段代码本身不复杂，但它代表了一类很重要的工程思维：你的循环不仅要处理「正常走完」，还要处理「在任意一步被掐断」。一个能用的 agent 和一个能交付的 agent，差距常常就在这些「半截状态怎么收尾」上。

## 和 Claude Code 的对照

把 CoreCoder 这 200 行和 Claude Code 的 `query.ts` 摆在一起，你会发现循环的骨架几乎重合：拼消息、带工具调模型、模型要工具就执行、结果回填、再循环、模型给文本就结束。这套结构不是 CoreCoder 抄来的，是这一代编码 agent 的共同范式，谁来写都长这样。

真正的差距在循环之外那一圈防护。Claude Code 的循环里裹着一层厚得多的错误恢复：限流了退避重试、上下文超了自动压缩再重试、服务端 529 了切换备用模型、输出被截断了重试若干次、还有更细的预算控制（既数轮次也数美元）。CoreCoder 也已把核心防线拆到外围模块：重试在 [`llm.py`](03-llm-and-cost.md) 里，压缩在 [`context.py`](04-context.md) 里，`ModelChain` 在一次请求耗尽自身重试后按顺序切换备用模型，`max_rounds` 限制工具调用轮数，`BudgetState` 则在已知价格时限制新的模型请求。形状相同，厚度仍然不同。本系列接下来几篇，基本就是逐个补上这圈防护，看每一层在 Claude Code 里是什么、在 CoreCoder 里被压成了几十行的什么。

## 收个尾

把这一篇压成一句话：agent 的核心就是一个有上限的循环，问模型、跑工具、回填结果、再问，直到模型只回文本。但真正决定它好不好用的，是循环边上那些不起眼的处理。模型自己决定何时收手，所以它的行为天生不像传统程序那样可控；工具调用和回复必须按 id 严格配对，这条约束会在中断和压缩时反复制造坑；至于区分「参数填错」和「工具自己炸了」、给循环加硬上限、给中断补占位回复，这些就是 demo 和可交付之间的真正距离。

下一篇，我们看是什么让这个循环真能动手：工具系统。
