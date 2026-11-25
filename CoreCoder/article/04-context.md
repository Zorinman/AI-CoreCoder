# 用有限的窗口，扛住一个长任务

agent 面临一项无法绕开的物理约束：上下文窗口有限。CoreCoder 通过 `CORECODER_MAX_CONTEXT_TOKENS` 配置窗口上限，默认值为 128000；无论请求由主模型还是备用模型处理，`Agent` 都使用同一个 `ContextManager` 管理对话历史。

编码任务又会迅速产生大量 token。模型读取一个一千行的文件，连同行号的内容都会进入历史；运行一次测试，数百行输出也会进入历史；一次 grep 可能再带来几十条匹配。一个稍有规模的任务执行十几轮后，就可能消耗数万 token。窗口一旦填满，API 可能直接报错；即使主动删减历史，处理不当也会让 agent「忘记」已读文件或已作出的决定。

因此，如何在有限窗口内承载长任务，是 agent 工程中最关键的问题之一。本篇通过 `corecoder/context.py` 说明 CoreCoder 的处理方式。

上下文的起点并不只有对话历史。Agent 启动时会将系统提示词和全部工具 Schema 一并发送给模型；启用 MCP 后，发现的外部工具也会加入这一部分。CoreCoder 会在启动时估算这部分占用：超过上下文窗口时直接拒绝启动，超过窗口 25% 时显示警告。MCP 工具集在启动时固定，Server 后续变更工具清单需要重启后才会重新计算和注册，因此一次运行中的上下文基线保持稳定。

## 分层，从轻到重

公开拆解显示，Claude Code 使用四层策略，从成本最低的处理逐步升级到最激进的处理。CoreCoder 将其收敛为三层，原则不变：能够用低成本方式回收的空间，就不提前使用高成本方式。三层分别在窗口达到一定占比时触发：

```python
self._snip_at = int(max_tokens * 0.50)      # 50% -> 截断臃肿的工具输出
self._summarize_at = int(max_tokens * 0.70)  # 70% -> LLM 摘要旧对话
self._collapse_at = int(max_tokens * 0.90)   # 90% -> 硬折叠，最后手段
```

`maybe_compress` 是调度者，它先估算当前用了多少 token，然后从轻到重地按需施加每一层，每施加一层就重新估一次，够了就停：

```python
def maybe_compress(self, messages, llm=None) -> bool:
    current = estimate_tokens(messages)
    compressed = False

    if current > self._snip_at:
        if self._snip_tool_outputs(messages):
            compressed = True
            current = estimate_tokens(messages)

    if current > self._summarize_at and len(messages) > 10:
        if self._summarize_old(messages, llm, keep_recent=8):
            compressed = True
            current = estimate_tokens(messages)

    if current > self._collapse_at and len(messages) > 4:
        self._hard_collapse(messages, llm)
        compressed = True

    return compressed
```

前两篇中出现的 `self.context.maybe_compress(...)` 调用，实际执行的就是这里的逻辑。它会在每次请求前和每轮工具执行后进行检查；多数情况下窗口尚有余量，函数会直接返回。压缩采用惰性策略，只在接近上限时才真正执行。

至于 token 的估算，`estimate_tokens` 采用了一个非常直接的方法：字符数除以 3。

```python
def _approx_tokens(text: str) -> int:
    """Rough token count, roughly 3 chars per token for mixed en/zh content."""
    return len(text) // 3
```

这个估算并不精确；若追求精确，必须引入 tokenizer。不过压缩判断需要的只是「当前大约使用了多少」的量级，字符数除以 3 对中英混合内容已足够，并且没有额外依赖和开销。这里选择的是轻量且够用的方案，而不是更精确但更重的实现。

## 第一层：旧的工具输出，是有保质期的

第一层 `_snip_tool_outputs` 是最便宜的，不调模型，纯文本处理。它把超过 1500 字符的工具结果，截成只留头三行和尾三行：

```python
content = m.get("content", "")
if len(content) <= 1500:
    continue
lines = content.splitlines()
if len(lines) <= 6:
    continue
snipped = (
    "\n".join(lines[:3])
    + f"\n... ({len(lines)} lines, snipped to save context) ...\n"
    + "\n".join(lines[-3:])
)
m["content"] = snipped
```

这一层背后有个我觉得很漂亮的洞察：工具输出是有保质期的。

二十轮之前那次 grep 吐出来的两百行匹配，在当时很有用，模型靠它定位了代码。但到了现在，模型早就用完那个结果、做完了相应的修改，那两百行就成了纯粹的占位垃圾，留着它只是白占窗口。把它截成头尾几行，既保留了「这里曾经查过一次、大致是这些文件」的线索，又把绝大部分死重量扔掉了。新鲜的信息值钱，陈旧的信息廉价，压缩就该优先压陈旧的。公开拆解里管这层叫 HISTORY_SNIP，干的是同一件事。

为什么截头尾而不是只截开头？因为命令输出最有用的信息常常在两端：开头是它在干什么，结尾是结果和报错。中间那一大坨过程往往可以丢。这个「留头尾、弃中间」的选择，和上一篇 bash 输出截断的逻辑是一脉相承的。

## 第二层：让模型给旧对话写个摘要

第一层只压工具输出，压不动对话本身。当窗口涨到 70%，第二层 `_summarize_old` 上场：把旧的对话整段交给模型写一个摘要，只留最近 8 条消息原样不动。

```python
split = self._safe_split(messages, keep_recent)
old = messages[:split]
tail = messages[split:]

summary = self._get_summary(old, llm)

messages.clear()
messages.append({
    "role": "user",
    "content": f"[Context compressed - conversation summary]\n{summary}",
})
messages.append({
    "role": "assistant",
    "content": "Got it, I have the context from our earlier conversation.",
})
messages.extend(tail)
```

旧对话被替换成一条「这是之前对话的摘要」的 user 消息，外加一条模型「我记下了」的 assistant 回应，然后接上原封不动的近期消息。摘要本身由 `_get_summary` 生成，它给模型的指令很聚焦：保留改过的文件路径、做过的关键决定、遇到的错误、当前任务状态；丢掉啰嗦的命令输出、代码清单、来回的废话。这正是一个长任务里真正需要被记住的东西。

如果没有可用的模型（或者摘要调用本身失败了），它退化成 `_extract_key_info`，用正则把文件路径和带 error 的行抽出来拼一个粗摘要。又是优雅降级：宁可给个糙摘要，也不让压缩这一步把整个会话拖垮。

## 那个一定会咬你的坑：孤儿 tool 消息

现在讲这一篇的重头戏，也是我在打磨这个项目时实打实踩过的坑。

回想第一篇那条铁律：一条带 `tool_calls` 的 assistant 消息，后面必须跟着配对的 `tool` 回复，少一个 API 都会拒。压缩这件事的本质，是在历史的某个位置切一刀，前面的压掉、后面的留下。问题来了：如果这一刀正好切在一组工具调用的中间呢？

设想历史是这样一段：assistant 发起了工具调用、紧跟着是对应的 tool 回复。如果「保留最近 N 条」这个边界恰好落在 tool 回复上，那么被保留的尾巴就会以一条 tool 消息开头，而产生它的那条 assistant 消息被切到前面、压进摘要里没了。这条 tool 回复成了孤儿，它前面找不到对应的 tool_calls。下一次请求带着这个孤儿发出去，API 当场拒绝。你的压缩逻辑本来是来救场的，结果亲手把会话搞死了。

`_safe_split` 就是来防这个的。它在切分前，把边界往前挪，直到边界那条消息不再是 tool

### 错误切分：假设应保留最近两条，初始切分点是 split = 2
messages[1] 被压缩，但 messages[2] 被保留。messages[2]["tool_call_id"] == "call_1"，却找不到对应的 messages[1]["tool_calls"]，因此它成为孤儿。
```text
old  = messages[:2]          tail = messages[2:]

messages[0]  messages[1]     │  messages[2]  messages[3]
┌──────────┐ ┌─────────────┐ │  ┌──────────┐ ┌──────────┐
│ user     │ │ assistant   │ │  │ tool     │ │ user     │
│          │ │ tool_calls  │ │  │ call_1   │ │          │
└──────────┘ └─────────────┘ │  └──────────┘ └──────────┘
                              ↑
                         切分点在 tool 上
```
### 安全切分：_safe_split() 发现 messages[2]["role"] == "tool"，将 split 减为 1
这样 messages[1] 与 messages[2] 会同时保留，工具调用和工具结果的关联完整。
```text
old = messages[:1]                  tail = messages[1:]

messages[0]                          │  messages[1]  messages[2]  messages[3]
┌──────────┐                         │  ┌──────────┐ ┌──────────┐ ┌──────────┐
│ user     │                         │  │ assistant│ │ tool     │ │ user     │
│          │                         │  │ tool_calls│ │ call_1  │ │          │
└──────────┘                         │  └──────────┘ └──────────┘ └──────────┘
                                      ↑
                             安全切分点在 assistant 前
```
```python
@staticmethod
def _safe_split(messages: list[dict], keep_recent: int) -> int:
    """Index where the kept tail should start.

    Walk the boundary back so a 'tool' result is never separated from the
    assistant message whose tool_calls produced it - an orphaned tool
    message has no preceding tool_calls and OpenAI-compatible APIs reject it.
    """
    split = max(0, len(messages) - keep_recent)
    while split > 0 and messages[split].get("role") == "tool":
        split -= 1
    return split
```

就这么一个 `while` 循环往回退。逻辑短得不能再短，但少了它，压缩就是个定时炸弹，平时不响，偏在长对话、窗口吃紧、最不该出事的时候炸。第二层和第三层切分时都走 `_safe_split`，不直接用 `len - keep_recent`。

这个坑值得你记住，因为它有一切「隐蔽 bug」的特征：它依赖一个跨越多条消息的不变式（tool 必须紧跟 tool_calls），这个不变式没写在任何一处显眼的地方，平时也不触发，只在「切分点恰好落在工具调用中间」这个特定时机才暴露。这种 bug 你很难靠盯着单个函数看出来，得在脑子里同时装着「压缩逻辑」和「API 的配对约束」两件事，才能意识到它们会在某个角落打架。CoreCoder 用两个测试把这个不变式钉死了，`test_safe_split_never_orphans_a_tool_message` 验切分点不落在 tool 上，`test_compress_never_leaves_an_orphan_tool_reply` 验整轮压缩后每条 tool 回复都还紧跟着它的 tool_calls。写这种测试，本质上是把一条「藏在脑子里的不变式」固化成代码，让它以后别再被人不小心破坏。

## 第三层：最后手段

窗口达到 90% 时，说明前两层压缩仍不足以释放空间，第三层 `_hard_collapse` 会介入。它只保留最后几条消息和一份摘要，其余内容全部折叠；同样通过 `_safe_split` 避免留下孤儿消息。这一层很少触发，目的在于优先保证会话可以继续运行，即使需要因此丢失更多上下文。

## 和 Claude Code 的对照

四层和三层的差别，主要在 Claude Code 多了一层带缓存的微压缩（microcompact）和周期性的后台自动压缩，工程上更精细。但「分层、从轻到重、惰性触发、优先压陈旧信息」这套核心思路，两者完全一致。CoreCoder 把它压成三层，刚好够你看清每一层在解决什么、代价是什么，而不至于淹没在缓存和调度的细节里。

这一篇其实也回答了一个开头的问题：为什么 agent 偶尔会「忘事」。因为它真的会忘，压缩就是有损的，被摘要掉的细节就是丢了。摘要请求也走当前的模型链，所以主模型不可用时同样可能切到备用模型；若摘要请求失败，代码会退回到本地抽取。好的压缩策略不是不丢，而是丢得聪明，优先丢那些已经没有保质期的东西。

