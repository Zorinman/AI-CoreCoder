## 八篇怎么读

1. [一个 agent 的本体，是一个 while 循环](01-the-loop.md)。整个 agent 最核心的东西，是一个「问模型、跑工具、把结果喂回去、再问」的循环。我们看 CoreCoder 的 `agent.py` 怎么把它写明白，以及打断、轮次上限、半截工具调用回填和预算拒绝这些真实世界的麻烦各自怎么收场。
2. [工具系统：让模型安全地动手](02-tools.md)。模型本身只会吐字，是工具让它能读文件、写文件、跑命令。这篇讲 CoreCoder 的七个内置工具，重点是那个看似平平无奇、实则是 Claude Code 关键创新的「唯一性搜索替换」编辑，以及 bash 的安全闸。默认工具现在由每个 `Agent` 自己创建，运行状态也随 agent 隔离；MCP 工具则在启动时追加。
3. [接入任意大模型，顺便把钱算清楚](03-llm-and-cost.md)。`llm.py` 怎么用 OpenAI 兼容接口和 LiteLLM 接住不同 provider，怎么做指数退避重试，怎么在流式输出里顺手把 token 和美元成本统计出来，并用主备模型链和硬预算兜底。
4. [用有限的窗口扛住一个长任务](04-context.md)。上下文窗口是 agent 的硬约束。`context.py` 实现了三层压缩，从轻到重。这篇还会讲一个特别容易踩、API 一定报错的坑：孤儿 tool 消息。这是我做这个项目时真改过的 bug。
5. [并行执行与子 agent](05-parallel-and-subagents.md)。模型一次返回多个工具调用时，CoreCoder 只让显式声明为并发安全的连续调用进入线程池，其余保持串行。这篇老实讲这个简化版相对 Claude Code 的流式执行器差在哪，并发又会引入什么新麻烦，以及子 agent 为什么不准递归。
6. [把它跑成一个真正的命令行工具](06-session-and-cli.md)。会话存盘、断点续聊、斜杠命令、一次性模式、预算和变更文件查询。`session.py` 里有个不起眼但很要命的安全细节：怎么防住用恶意会话名做路径穿越。
7. [Fork CoreCoder，搭一个你自己的 coding agent](07-build-your-own.md)。实操篇。从 clone 到换成你常用的模型，到加一个真正有用的自定义工具，到改系统提示词调教它的风格，到打包发布。读完前六篇你已经懂了原理，这篇让你真有一个东西。
8. [MCP 客户端：让 agent 受控地接入外部工具](08-mcp-client.md)。MCP 配置为什么必须由用户显式授权、stdio 和 Streamable HTTP 如何共存、异步会话如何适配同步 Tool 接口，以及工具快照、命名空间、超时和外部费用的边界。

不想按顺序也行。想搞懂「它凭什么敢自动跑命令」直接跳第二篇；想接自己的模型直接跳第三篇；只想赶紧 fork 出个能用的，第七篇是自洽的。

## 五分钟先把它跑起来

阅读前，先在自己的机器上运行一次，后面的代码才更容易建立直觉。

```bash
git clone https://github.com/he-yufeng/CoreCoder
cd CoreCoder
pip install -e .
```

然后复制配置模板，填写主模型和 key。主模型的 `MODEL` 与 `API_KEY` 是必填项；默认后端走 OpenAI 兼容接口，非兼容 provider 则改为 LiteLLM 后端：

```bash
cp .env.example .env

# .env：DeepSeek 的 OpenAI 兼容接口
CORECODER_PRIMARY_MODEL=deepseek-chat
CORECODER_PRIMARY_API_KEY=sk-...
CORECODER_PRIMARY_BASE_URL=https://api.deepseek.com/v1

# 或者本地 Ollama
CORECODER_PRIMARY_MODEL=qwen2.5-coder
CORECODER_PRIMARY_API_KEY=ollama
CORECODER_PRIMARY_BASE_URL=http://localhost:11434/v1
```

跑起来：

```bash
corecoder
```

进入后可以随便给它一个任务，例如「读一下 corecoder/agent.py，告诉我这个循环最多转多少轮」。它会先调用 `read_file`，再依据读取结果作答。这一过程与 Claude Code 的基本工作方式并无本质区别。后续文章会逐步拆开这套机制，并说明如何受控地扩展工具能力。


---

