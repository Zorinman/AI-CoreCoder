# Fork CoreCoder，搭建属于你自己的 coding agent

前六篇已经分别拆解了 CoreCoder 的循环、工具、模型接口、上下文压缩、并发、子 agent 和 CLI。本篇将这些部分重新组合，带你构建属于自己的 coding agent。

从理解原理到亲手完成实现，只差一次实践。读完本篇后，你将拥有一个能够运行、接入常用模型、包含自定义工具，并按自身需求调整过的 coding agent。

## 第零步：建立通过的测试基线

先确保项目的初始状态正常。

```bash
# 在 GitHub 上 fork he-yufeng/CoreCoder 到你自己名下，然后
git clone https://github.com/<你的用户名>/CoreCoder
cd CoreCoder
pip install -e .
python -m pytest tests/ -q
```

运行最后一行后，应看到 91 个测试全部通过。不要跳过这一步：它用于确认环境处于干净、可用的初始状态。之后如果出现问题，才能判断它由本次改动引起，而不是环境原本就存在异常。项目自带的测试是重构时的安全网；前文讨论的孤儿 tool 消息、分段并发调度、主备模型配置和路径穿越等问题，都有对应测试覆盖。每完成一处修改，都应重新运行这条命令。

## 第一步：接入你自己的模型

`.env.example` 使用 `gpt-5.4-mini` 作为示例主模型，但运行时不会隐式提供默认模型：必须明确配置 `CORECODER_PRIMARY_MODEL` 和 `CORECODER_PRIMARY_API_KEY`。如第三篇所述，更换模型只需调整主模型配置。开发阶段建议先使用本地 Ollama，它无需付费，也便于反复试验。

```bash
# 装个 Ollama，拉一个 coder 模型
ollama pull qwen2.5-coder

cp .env.example .env
# 在 .env 中填写：
CORECODER_PRIMARY_MODEL=qwen2.5-coder
CORECODER_PRIMARY_API_KEY=ollama
CORECODER_PRIMARY_BACKEND=openai_compatible
CORECODER_PRIMARY_PROVIDER=ollama
CORECODER_PRIMARY_BASE_URL=http://localhost:11434/v1

corecoder
```

本地模型的能力通常不及旗舰模型，但足以验证「改动是否能让流程正常运行」，也能避免每次调试工具都产生 API 调用成本。确认逻辑无误后，再将配置切换为 DeepSeek 或其他模型，检查实际效果。

## 第二步：添加一个实用工具

第二篇中添加的 `now` 工具只用于查询时间。本节实现一个更实用的工具，使 agent 能够获取网页或 API 返回的文本内容，从而读取在线文档和查看接口响应。

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
    # 没有共享可变状态，允许与其他只读工具同段并发。
    concurrency_safe = True

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

在 `corecoder/agent.py` 中导入它，并把它加入 `_create_default_tools()`：

```python
from .tools.fetch import FetchUrlTool

def _create_default_tools(self) -> list[Tool]:
    state = self._runtime_state
    return [
        BashTool(state), ReadFileTool(), WriteFileTool(state),
        EditFileTool(state), GlobTool(), GrepTool(), AgentTool(), FetchUrlTool(),
    ]
```

启动后，可以向它提问：“抓一下 https://raw.githubusercontent.com/he-yufeng/CoreCoder/main/README.md，看看这个项目是做什么的。”它会调用 `fetch_url`，再根据结果作答。

这个小工具综合了前几篇的多项原则。文本过长时保留首尾，与第二篇和第四篇使用的截断策略一致；`errors="replace"` 负责处理解码异常，较宽的 `try-except` 则将各类异常转换为清晰的错误信息，避免把坏数据的问题转嫁给用户。第五篇还提出一个关键问题：工具能否承受并发调用？`fetch_url` 没有共享可变状态，每次调用只依赖传入的 URL 并独立返回结果，因此可以明确声明 `concurrency_safe = True`。只有能够证明工具不存在共享状态或并发风险时，才应打开这一声明。

不过，正如第二篇讨论 bash 时所强调的，也必须明确这个工具的一个弱点。`fetch_url` 存在一种真实的安全风险：SSRF。它可以访问任意 URL，包括 `http://localhost`、内网地址，以及云环境中臭名昭著的 `http://169.254.169.254` 元数据服务。若只在自己的机器上使用，风险相对可控；但一旦将 agent 用于会执行陌生人指令的场景，这个工具就可能成为探测内网的入口。要降低风险，至少需要在 `execute` 中解析 URL 主机名所指向的 IP 地址，并拦截环回、私有、链路本地等保留地址。这里故意不实现这部分，以便让你清楚看到这一风险，而不是将其隐藏起来、假装不存在。每添加一个能向外部发起请求的工具，都应先问一句：“它在最坏情况下会被怎样滥用？”这种习惯比任何一段具体的防护代码都更有价值。

## 第三步：调校它的行为风格

agent 的行为风格主要由系统提示词塑造，而不是由某段 `if-else` 决定。打开 `corecoder/prompt.py`，其中的 `# Rules` 就是 agent 的行为准则。原版包含以下几条：

```
1. Read before edit. 改文件前先读。
3. Verify your work. 改完跑相关测试确认。
4. Be concise. 多给代码，少废话。
```

这些规则会直接影响它的工作方式。若希望它更谨慎，可加入“执行破坏性操作前先征求确认”；若希望它使用中文注释，可加入“代码注释一律使用中文”；若希望它每次修改后都运行测试，就将第 3 条写得更明确。提示词只需改动一行，整体行为就可能随之变化。这是 agent 工程中投入产出比较高的调优手段，往往比修改底层代码更快见效，值得作为产品的一部分持续打磨。

## 第四步：不只作为 CLI 使用，也可将它作为库使用

CoreCoder 在顶层导出了 `Agent`、`LLM`、`Config`、`ModelConfig`、`ModelChain` 和 `BudgetState`。这意味着你不必局限于它的交互式终端，还可以将它作为库使用，构建形态完全不同的 agent。

举一个例子。第一篇和第五篇提到过，每个 `Agent` 只识别自己配置的工具集合。利用这一点，可以构建一个“只读”代码审查 agent：从机制上，它无法修改文件或执行命令，因为根本没有为它配置写入工具或 bash 工具：

```python
from corecoder import Agent, Config, ModelChain
from corecoder.tools.glob_tool import GlobTool
from corecoder.tools.grep import GrepTool
from corecoder.tools.read import ReadFileTool

config = Config.from_env()
llm = ModelChain.from_config(config)

# 只给读、搜、找文件这三样，没有 write_file，没有 bash
reviewer = Agent(
    llm=llm,
    tools=[ReadFileTool(), GrepTool(), GlobTool()],
    max_rounds=15,
)

report = reviewer.chat(
    "审查 corecoder/agent.py，找出并发相关的隐患，列成一份清单。"
)
print(report)
```

这个 `reviewer` 的权限已经收敛到适合自动化流程的范围。它可以读取和搜索整个代码库，却无法修改文件或执行命令，因为这些工具根本不在它的工具集中。传入 `tools=[...]` 时，这份列表会完整替换默认工具，不会自动补回 `bash` 或写入工具。这正是第一篇强调“工具集是实例级的”所揭示的用法：约束 agent 能做什么，最可靠的方式不是写一长串规则要求它不要越界，而是从源头上不赋予它这些能力。工具集就是它的能力边界。

将它作为库使用，能构建的远不止 CLI：运行在 CI 中的审查 bot、接入 Web 后端的对话接口，或批量处理多个仓库的脚本。核心模块保持紧凑，至于外层采用何种形态，完全由你决定。

## 第五步：为你的改动补充测试

添加 `fetch_url` 后，顺手为它写一个测试，并与项目中其他工具的测试保持一致。在 `tests/test_tools.py` 中添加：

```python
def test_fetch_rejects_non_http():
    from corecoder.tools.fetch import FetchUrlTool

    fetch = FetchUrlTool()
    r = fetch.execute(url="file:///etc/passwd")
    assert "only http" in r
```

然后运行 `python -m pytest tests/ -q`，测试数量会从 91 个增加到 92 个。

之所以单独列出这一步，是因为它是区分“随手尝试”和“认真开发”的分水岭。前六篇中最有价值的设计——对孤儿 tool 消息的防御、并发安全和路径穿越的双重防护——都有相应的测试。这些测试并非为了做样子，而是将“藏在脑海中的不变式”固化下来；无论是三个月后的自己，还是后来接手的人，只要改坏了，便能立即发现。为 agent 添加的每一项能力，都值得用一个测试来明确它的边界。这也是本项目希望顺带传达给你的工作习惯。

## 还可以继续扩展的方向

CoreCoder 是起点，而非终点。它有意留下了不少可扩展之处，前几篇也大多提到过相应的方向：

- **为 bash 加上真正的沙箱**。第二篇已说明，正则黑名单只能避免误操作，不能构成安全边界。若要处理不可信输入，必须使用 `seccomp` 或容器级隔离。
- **完善成本策略**。主备模型链和已知价格模型的硬预算已经具备；若业务要求严格成本控制，可补全所用模型的价格数据，并决定未知价格模型是继续放行还是直接拒绝。
- **细化并发策略**。CoreCoder 已按 `concurrency_safe` 做保守的分段调度；若有真实需求，可继续加入依赖分析、资源锁或更细的工具级并发限制。
- **接入 MCP**。让 agent 接入 Model Context Protocol 工具生态，便能使用大量现成的外部能力。
- **为子 agent 提供更多运行模式**。第五篇提到，Claude Code 的子 agent 可在独立 worktree 或后台运行，而 CoreCoder 目前仅提供最基础的同步模式。

只需选择一个确有需求的方向开始即可。无需因为清单很长而焦虑：agent 的核心足够小，一个人可以在一个周末读透；它的扩展空间又足够大，能够向不同方向继续演进。

## 写在最后

如果这个系列只能留下一个结论，我希望是：编码 agent 并没有想象中那么神秘，它只是由一系列可以理解、也可以亲手实现的工程决策组成。

回顾前六篇，它其实只包含几部分：一个带有上限、预算保护和备用模型切换的循环（第一篇和第三篇），以及一组接口清晰、让它能够执行操作的工具（第二篇）。模型接口通过 OpenAI 兼容层与 LiteLLM 接住不同 provider，上下文则通过分层压缩来对抗遗忘（第四篇）。它能派出子 agent、支持按安全属性分段并发，依赖于对共享状态的谨慎处理（第五篇）；最外层的 CLI 负责事件输出和界面呈现，同时也封堵了路径穿越的漏洞（第六篇）。这些模块没有任何一处是你无法理解的。

现在，你不仅已经看懂了它，还 fork 了它、接入了自己的模型、添加了自己的工具，并调校了它的行为风格。现在，它属于你了。

去为它添加一些别人没有的能力吧。
