# Fork CoreCoder，搭建属于你自己的 coding agent

前六篇中，我们逐一拆解了 CoreCoder：循环、工具、模型接口、上下文压缩、并发、子 agent 和 CLI，每个组成部分都已单独讲解。本篇将把它们重新组合起来，打造属于你自己的 coding agent。

从读懂到亲手做出来，只差一次实践。本篇将带你迈过这一步。读完后，你将拥有一个能够运行、接入常用模型、配有亲手添加的工具，并按自身需求调校过的 coding agent。

## 第零步：建立全绿的测试基线

先确保项目的初始状态正常。

```bash
# 在 GitHub 上 fork he-yufeng/CoreCoder 到你自己名下，然后
git clone https://github.com/<你的用户名>/CoreCoder
cd CoreCoder
pip install -e .
python -m pytest tests/ -q
```

运行最后一行后，应看到 86 个测试全部通过。不要跳过这一步：它能确认你的环境处于干净、可用的初始状态。之后如果修改出现问题，才能确认问题由你的改动引起，而不是环境原本就存在异常。项目自带的测试并非摆设，而是重构时的安全网。前几篇反复提到的孤儿 tool 消息、并发竞态和路径穿越等问题，都有相应测试覆盖。之后每完成一处修改，都应重新运行这条命令。

## 第一步：接入你自己的模型

CoreCoder 默认使用 `gpt-5.5`，但你未必想使用它。第三篇讲过，更换模型只需修改环境变量。在开发阶段，我强烈建议先使用本地 Ollama：无需付费，也方便反复试验。

```bash
# 装个 Ollama，拉一个 coder 模型
ollama pull qwen2.5-coder

export OPENAI_API_KEY=ollama
export OPENAI_BASE_URL=http://localhost:11434/v1
export CORECODER_MODEL=qwen2.5-coder

corecoder
```

本地模型的能力通常不及旗舰模型，但用于验证“改动是否能让流程正常跑通”已经足够，也能避免每调试一次工具都产生 API 调用成本。待逻辑验证无误后，再将环境变量改为 DeepSeek 或其他模型对应的配置，查看实际效果。

## 第二步：添加一个实用工具

第二篇中，我们添加过一个用于查询时间的 `now`，功能较为简单。这次我们添加一个更实用的工具：让 agent 能够获取网页或 API 返回的文本内容。有了它，agent 就可以读取在线文档、查看接口的返回内容，能力范围也随之扩展。

创建文件 `corecoder/tools/fetch.py`：

```python
"""A read-only tool that fetches the text content of a URL."""

import urllib.request
from .base import Tool


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Fetch the text content of an http(s) URL. "
        "Use this to read documentation, API responses, or web pages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The http:// or https:// URL to fetch",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 15)",
            },
        },
        "required": ["url"],
    }

    def execute(self, url: str, timeout: int = 15) -> str:
        if not url.startswith(("http://", "https://")):
            return "Error: only http and https URLs are supported"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CoreCoder"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(1_000_000)  # 顶多读 1MB，别让一个巨页撑爆内存
                text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return f"Error fetching {url}: {e}"
        # 太长就留头尾，跟 bash 输出截断一个套路
        if len(text) > 8000:
            text = text[:6000] + f"\n... (truncated, {len(text)} chars) ...\n" + text[-1000:]
        return text
```

在 `tools/__init__.py` 中注册：

```python
from .fetch import FetchUrlTool

ALL_TOOLS = [
    BashTool(),
    # ...原有的...
    FetchUrlTool(),
]
```

启动后，向它提问：“抓一下 https://raw.githubusercontent.com/he-yufeng/CoreCoder/main/README.md，看看这个项目是做什么的。”它会调用 `fetch_url`，再把结果告诉你。

这个小工具实际用到了前几篇中的多项经验。文本过长时保留首尾的截断策略，在第二篇和第四篇中多次出现。使用 `errors="replace"` 解码，并用范围较广的 try-except 块将各类异常转换为易懂的错误信息，也体现了此前一贯坚持的原则：不要把坏数据的问题转嫁给用户。还有第五篇最值得关注的一点：工具能否承受并发调用？`fetch_url` 可以，因为它没有共享的可变状态；每次调用都只依赖传入的 URL，并独立返回结果，两个线程同时运行也互不干扰。这并非偶然，而是设计使然。工具可以设计为无状态时，就不要为它引入状态；这是让工具默认具备并发安全性的省力做法。

不过，正如第二篇讨论 bash 时所强调的，也必须明确这个工具的一个弱点。`fetch_url` 存在一种真实的安全风险：SSRF。它可以访问任意 URL，包括 `http://localhost`、内网地址，以及云环境中臭名昭著的 `http://169.254.169.254` 元数据服务。若只在自己的机器上使用，风险相对可控；但一旦将 agent 用于会执行陌生人指令的场景，这个工具就可能成为探测内网的入口。要降低风险，至少需要在 `execute` 中解析 URL 主机名所指向的 IP 地址，并拦截环回、私有、链路本地等保留地址。这里故意不实现这部分，以便让你清楚看到这一风险，而不是将其隐藏起来、假装不存在。每添加一个能向外部发起请求的工具，都应先问一句：“它在最坏情况下会被怎样滥用？”这种习惯比任何一段具体的防护代码都更有价值。

## 第三步：调校它的行为风格

agent 的行为风格主要不是由某段 if-else 决定，而是由系统提示词塑造。打开 `corecoder/prompt.py`，其中的 `# Rules` 就是 agent 的“行为准则”。原版包含以下几条：

```
1. Read before edit. 改文件前先读。
3. Verify your work. 改完跑相关测试确认。
4. Be concise. 多给代码，少废话。
```

这些规则会直接塑造它的工作方式。若希望它更谨慎，可加入“执行破坏性操作前先征求确认”；若希望它使用中文注释，可加入“代码注释一律使用中文”；若希望它每次修改后都运行测试，就将第 3 条写得更明确。提示词只需改一行，它的整体工作习惯便会随之改变。这是 agent 工程中性价比最高的调参手段之一，往往比修改代码更快见效。值得专门留出时间，将这段提示词视为产品的一部分来打磨。

## 第四步：不只作为 CLI 使用，也可将它作为库使用

CoreCoder 在顶层导出了 `Agent`、`LLM` 和 `Config`。这意味着你不必局限于它的交互式终端，还可以将它作为库使用，构建形态完全不同的 agent。

举一个例子。第一篇和第五篇提到过，每个 `Agent` 只识别自己配置的工具集合。利用这一点，可以构建一个“只读”代码审查 agent：从机制上，它无法修改文件或执行命令，因为根本没有为它配置写入工具或 bash 工具：

```python
from corecoder import Agent, LLM
from corecoder.tools import get_tool

llm = LLM(
    model="deepseek-chat",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)

# 只给读、搜、找文件这三样，没有 write_file，没有 bash
reviewer = Agent(
    llm=llm,
    tools=[get_tool("read_file"), get_tool("grep"), get_tool("glob")],
    max_rounds=15,
)

report = reviewer.chat(
    "审查 corecoder/agent.py，找出并发相关的隐患，列成一份清单。"
)
print(report)
```

这个 `reviewer` 的权限已收敛到适合用于自动化流程的程度。它可以读取并搜索整个代码库，但无法修改任何字节，也无法执行任何命令，因为这些工具根本不在它的工具集合中。这正是第一篇强调“工具集是实例级的”所揭示的用法：约束一个 agent 能做什么，最干净的方式不是写出一长串规则要求它不要越界，而是从源头上不赋予它这些能力。它拥有的工具，就是其能力的全部边界。

将它作为库使用，能构建的远不止 CLI：运行在 CI 中的审查 bot、接入 Web 后端的对话接口，或批量处理多个仓库的脚本。核心代码只有一千余行，至于外层采用何种形态，完全由你决定。

## 第五步：为你的改动补充测试

添加 `fetch_url` 后，顺手为它写一个测试，并与项目中其他工具的测试保持一致。在 `tests/test_tools.py` 中添加：

```python
def test_fetch_rejects_non_http():
    fetch = get_tool("fetch_url")
    r = fetch.execute(url="file:///etc/passwd")
    assert "only http" in r
```

然后运行 `python -m pytest tests/ -q`，测试数量会从 86 个增加到 87 个。

之所以单独列出这一步，是因为它是区分“随手尝试”和“认真开发”的分水岭。前六篇中最有价值的设计——对孤儿 tool 消息的防御、并发安全和路径穿越的双重防护——都有相应的测试。这些测试并非为了做样子，而是将“藏在脑海中的不变式”固化下来；无论是三个月后的自己，还是后来接手的人，只要改坏了，便能立即发现。为 agent 添加的每一项能力，都值得用一个测试来明确它的边界。这也是本项目希望顺带传达给你的工作习惯。

## 还可以继续扩展的方向

CoreCoder 是起点，而非终点。它有意留下了不少可扩展之处，前几篇也大多提到过相应的方向：

- **为 bash 加上真正的沙箱**。第二篇已说明，正则黑名单只能避免误操作，不能构成安全边界。若要处理不可信输入，必须使用 `seccomp` 或容器级隔离。
- **补充备用模型和以美元计的硬性预算上限**。第三篇提到，CoreCoder 有意未提供这两项能力，因为它们会引入服务商专属逻辑。若要进行生产部署，这两项能力迟早需要补上。
- **细化并发策略**。第五篇指出，可以通过区分工具属于“读”还是“写”来决定是否允许并发执行。CoreCoder 尚未实现，值得认真补全。
- **接入 MCP**。让 agent 接入 Model Context Protocol 工具生态，便能使用大量现成的外部能力。
- **为子 agent 提供更多运行模式**。第五篇提到，Claude Code 的子 agent 可在独立 worktree 或后台运行，而 CoreCoder 目前仅提供最基础的同步模式。

选择一个真正需要的方向着手即可。不必因清单很长而焦虑：agent 的魅力恰恰在于，它的核心小到一个人用一个周末就能读透，而扩展空间又足够大，可以朝任意方向延伸。

## 写在最后

如果这个系列只能让你记住一件事，我希望是：编码 agent 并没有想象中那么神秘；它只是由一系列你完全能够理解并亲手做出的工程决策构成。

回顾前六篇，它其实只包含几部分：一个带有上限的循环（第一篇），以及一组接口清晰、让它能够执行操作的工具（第二篇）。模型接口只是薄薄的一层服务商封装（第三篇），上下文则通过分层压缩来对抗遗忘（第四篇）。它能派出子 agent、支持并发，依赖于对共享状态的谨慎处理（第五篇）；最外层的 CLI 负责事件输出和界面呈现，同时也封堵了路径穿越的漏洞（第六篇）。引擎合计只有一千余行，连同这层 CLI 外壳，整个包也只有 1714 行；其中没有任何一处是你无法理解的。

现在，你不仅已经看懂了它，还 fork 了它、接入了自己的模型、添加了自己的工具，并调校了它的行为风格。现在，它属于你了。

去为它添加一些别人没有的能力吧。
