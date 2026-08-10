# 工具系统：让模型安全地动手

上一篇介绍主循环时，执行工具这一环节只是一笔带过；这一篇专门展开说明。

模型本身只能根据上下文生成文本，不能读取文件、运行测试，也不能向磁盘写入内容。工具让它从「会说」变成「会做」，也是 agent 与外部环境发生实际交互的通道。因此，工具接口是否清晰、错误反馈是否准确、危险操作能否被拦截，直接影响 agent 的能力和可靠性。

CoreCoder 默认提供七个内置工具：`bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep` 和 `agent`。调用方还可以在保留这套基础工具的前提下追加外部工具；CLI 使用这一入口接入 MCP。本文先介绍内置工具共享的接口，再重点分析两个关键工具，最后实现一个自定义工具。

## 一个工具长什么样

所有工具继承自 `tools/base.py` 里的 `Tool`：

```python
class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the function args

    # 默认不允许与其他工具并发执行；只读、无共享状态的工具显式改为 True。
    concurrency_safe: bool = False

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

一个工具由名字、供模型理解的描述、参数 JSON Schema、实际执行的 `execute` 方法，以及并发声明组成。`concurrency_safe` 默认值为 `False`；只有 `read_file`、`glob`、`grep` 等只读且没有共享可变状态的工具，会显式设为 `True`。`schema()` 将名称、描述和参数拼装为 OpenAI function calling 所需的格式，模型据此决定是否调用工具以及如何传参。

这里有个设计选择值得点一句。CoreCoder 没有搞工具的继承体系，没有 `FileTool` 派生 `ReadTool` 派生什么的。每个工具就是 `Tool` 的一个直接子类，自己管自己。Claude Code 走得更彻底，公开拆解里它压根不用 class 继承，而是用一个 `buildTool()` 工厂函数，把名字、schema、执行逻辑、权限检查当作配置传进去，拼出一个工具对象。两者背后是同一个判断：工具之间没什么真正可共享的行为，硬套继承只会增加耦合。组合优于继承，在这种地方体现得特别干净。

默认工具不再在模块导入时注册成一个全局列表。它们由 `Agent._create_default_tools()` 在构造每个 agent 时新建：

```python
def _create_default_tools(self) -> list[Tool]:
    state = self._runtime_state
    return [
        BashTool(state), ReadFileTool(), WriteFileTool(state),
        EditFileTool(state), GlobTool(), GrepTool(), AgentTool(),
    ]
```

这里的 `state` 保存当前工作目录和已修改文件。`bash`、`write_file`、`edit_file` 共用同一个 agent 私有状态；两个不同的 agent 不会互相覆盖工作目录、改动记录或 `AgentTool` 的父引用。想扩展 CLI 的默认能力，就往这个工厂方法里加一个新实例。这件事我们留到本篇最后真做一次。MCP 工具不进入这个工厂：CLI 在启动时发现它们，并以 `extra_tools` 追加到每个运行中的 Agent。

这也意味着旧的进程级 `ALL_TOOLS` 和 `get_tool` 入口已经删除，不提供兼容层。工具的所有权只从 `Agent` 实例或调用者显式传入的列表中确定，不能再通过全局注册表取得一个共享实例。

## edit_file：一个看着平平无奇的关键创新

七个内置工具中，最值得展开的是 `edit_file`。让模型修改已有文件看似简单，实际已经淘汰过多种不可靠的方案。

第一条死路是让模型按行号打补丁，比如「把第 42 行换成这样」。问题是模型对行号的感知极不可靠，它脑子里的第 42 行和文件里真实的第 42 行经常对不上，差一行就改错地方。而且只要文件在前面被动过一次，后面所有行号全部漂移。

第二条死路是让模型把整个文件重写一遍发回来。小文件还行，文件一大，又慢又贵，而且模型在「誊抄」那些它不该动的部分时，会时不时手滑改掉一两个字符，你还很难发现。

第三条死路是让模型生成标准的 diff/patch 格式。听上去优雅，实际上模型生成那种带 `@@ -42,7 +42,8 @@` 行号头的统一 diff，错误率高得让人头疼，那套上下文行数和偏移量它算不准。

Claude Code 的解法，也是 CoreCoder 照搬的解法，是第四条路：搜索替换，外加一个唯一性约束。模型给出一段「要找的原文」和「替换成的新文」，工具在文件里找这段原文，要求它恰好出现一次，然后替换。看 `tools/edit.py` 的核心：

```python
occurrences = content.count(old_string)

if occurrences == 0:
    preview = content[:500] + ("..." if len(content) > 500 else "")
    return (
        f"Error: old_string not found in {file_path}.\n"
        f"File starts with:\n{preview}"
    )
if occurrences > 1:
    return (
        f"Error: old_string appears {occurrences} times in {file_path}. "
        f"Include more surrounding lines to make it unique."
    )

new_content = content.replace(old_string, new_string, 1)
```

关键就在「恰好一次」这个约束。

如果原文一次都没找到，说明模型记错了内容，工具不会瞎猜，而是把文件开头贴回去让它重新对照。如果原文出现了不止一次，工具拒绝执行，因为它没法确定模型想改的是哪一处，于是回一句「这段文字出现了 N 次，多带几行上下文让它唯一」。这句话不只是报错，它是在教模型怎么把请求修对：下一轮模型会自觉地把 `old_string` 扩到包含足够的上下文，直到它在全文里唯一。

这一约束将模糊的「改文件」转化为确定的操作。模型不需要理解行号或计算偏移量，只需原样引用准备修改的内容，再由工具保证这段引用没有歧义。Claude Code 的系统提示词会明确要求 `old_string` 在文件中唯一，CoreCoder 也采用同一规则。这是以约束换取可靠性的典型设计。

替换完，工具还会生成一段统一 diff 返回给模型和用户：

```python
diff = _unified_diff(content, new_content, str(p))
return f"Edited {file_path}\n{diff}"
```

这里的方向恰好相反：让模型生成 diff 往往不可靠，而由工具生成 diff 则完全可控。模型可以根据这段 diff 确认修改是否符合预期。生成交给工具，理解和决策交给模型，职责因此更加清晰。

还有个容易被忽略的边角：编辑前先确认文件是 UTF-8 文本。

```python
try:
    content = p.read_text(encoding="utf-8")
except UnicodeDecodeError:
    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"
```

要是没这道判断，模型一不小心对一个二进制文件做 `edit_file`，得到的会是一大坨 Python 解码异常栈，既污染上下文又毫无帮助。有了它，模型拿到的是一句它读得懂的话。给模型的每一条错误信息，都该是「人话」，这是贯穿 CoreCoder 所有工具的一条隐性准则。

## bash：把危险操作拦在门外，但别假装这是沙箱

`read_file`、`edit_file` 这些工具能造成的破坏有限。`bash` 不一样，它能跑任意 shell 命令，模型一旦写出 `rm -rf /`，后果是真实的。

Claude Code 的 `BashTool` 公开拆解里是 1143 行，里头有命令分类器、有基于 `sandbox-exec` 和 `seccomp` 的真沙箱、有输出截断、有交互式命令拦截。CoreCoder 的 `bash.py` 是一个更小的蒸馏版，保留了四件最要紧的事：危险命令检测、输出截断、超时、工作目录跟踪。

危险命令检测是一张正则黑名单：

```python
_DANGEROUS_PATTERNS = [
    # recursive delete aimed at root/home (force flag optional)
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    # recursive (-r/-R) and force (-f) flags together, in any order or spacing
    (r"\brm\b(?=(?:.*\s)?-\w*[rR])(?=(?:.*\s)?-\w*f)", "force recursive delete"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    # ...还有块设备覆写、chmod 777 根目录、fork 炸弹、curl/wget 管道执行等
]
```

执行前先过一遍这张表，命中就直接拦下，连命令都不会真的跑：

```python
warning = _check_dangerous(command)
if warning:
    return f"⚠ Blocked: {warning}\nCommand: {command}\n..."
```

这两条针对 `rm` 的正则值得展开说说，因为它们反映了黑名单规则是否充分考虑了实际风险。第一条用于拦截对根目录或家目录的递归删除；`-f` 被设为可选，是因为 `rm -r /` 即使不带 `-f` 也同样危险。第二条通过两个前瞻断言检查命令中是否同时包含递归选项 `-r`（或 `-R`）和强制选项 `-f`，不受选项顺序或组合写法影响。这样，`rm -rf`、`rm -fr`、`rm -r -f` 和 `rm -f -r` 都会被识别。相应测试还覆盖了 `--recursive --force` 等长选项写法，同时确保 `rm -f notes.log`、`rm -r ./build_output` 这类常见命令不会被误拦截。

这里要把一条边界划清楚：**这张黑名单不是安全边界，它只是一道防手滑的闸**。正则黑名单天生拦不住有心人，命令可以 base64 编码，可以从变量里拼，可以用一百种方式绕过去。它能挡住的是模型一时糊涂生成的那种最常见、最直白的灾难命令，挡不住蓄意攻击。Claude Code 之所以要上 `seccomp` 这种内核级沙箱，正是因为黑名单这条路在安全上走不通。CoreCoder 选择黑名单，是在「教学清晰度」和「真实安全」之间做的明确取舍：它让你一眼看懂「危险拦截」这个设计点长什么样，但它没假装自己是生产级的安全方案。如果你拿 CoreCoder 去接一个不可信的使用场景，沙箱是你必须自己补上的一课。这点[第七篇](07-build-your-own.md)还会回来谈。

剩下两件事也顺带提一句。输出截断保留头尾，命令吐出几万行时只留前 6000 字符和后 3000 字符，中间用一行说明顶替，既不撑爆上下文又保住了最有用的开头和结尾。工作目录跟踪让 `cd` 在多次命令之间能记住，`_update_cwd` 还专门处理了 `cd a && cd b` 这种链式跳转，让 b 相对 a 解析而不是相对起点（测试 `test_bash_chained_cd_resolves_sequentially` 盯着它）。这些都是「跑命令」这件事在真实使用里会冒出来的小坑，一个一个填掉。

## 两阶段：先验形状，再验安全

把上一篇和这一篇连起来看，CoreCoder 对一次工具调用其实做了两道关。第一道在 `agent._exec_tool` 里，用 `inspect.signature().bind()` 验参数能不能对上函数签名，这是验「形状」。第二道在工具内部，比如 `bash` 的危险命令检测、`edit_file` 的 UTF-8 判断，这是验「该不该真做」。

这正对应 Claude Code 的两阶段门控，公开拆解里叫 `validateInput` 和 `checkPermissions`：一个验输入合不合法，一个验这个操作允不允许。把「格式对不对」和「该不该做」分成两关，好处是各自的失败能给出各自精准的反馈，模型也能针对性地修正。一个混在一起的大 try-except 做不到这种精度。

## 动手：写你自己的第一个工具

说完设计，再实现一个工具。假设要让 agent 查询当前时间，而模型本身并不知道此刻的时间，可以新建 `corecoder/tools/now.py`：

```python
"""A tool that tells the agent the current time."""

import time
from .base import Tool


class NowTool(Tool):
    name = "now"
    description = "Get the current local date and time. Use this when the user asks about the current time or you need a timestamp."
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")
```

然后在 `agent.py` 导入它，并把它加入 `_create_default_tools()` 返回的列表：

```python
from .tools.now import NowTool

def _create_default_tools(self) -> list[Tool]:
    state = self._runtime_state
    return [
        BashTool(state), ReadFileTool(), WriteFileTool(state),
        EditFileTool(state), GlobTool(), GrepTool(), AgentTool(), NowTool(),
    ]
```

完成后重新运行 `corecoder`，询问「现在几点」，它就会调用 `now` 并根据结果作答。

回头看你写了什么。`name` 是模型用来点名调用的标识。`description` 是模型决定「什么时候该用这个工具」的唯一依据，所以这句话要写得像在跟一个聪明但没有先验知识的同事交代，告诉它这工具干嘛、什么场景下用。`parameters` 是空的，因为查时间不需要参数，但如果你的工具需要参数，这里就是那份 JSON Schema，模型照着它填。`execute` 返回一个字符串，这个字符串会原样变成一条 `tool` 消息喂回模型。

整个工具系统的可扩展性就浓缩在这几步里：定义一个类，登记一下，模型立刻就会用了。你给 agent 长出一只新的手，成本低到这种程度。[第七篇](07-build-your-own.md)我们会写一个比查时间有用得多的工具，把这套机制用在刀刃上。

## 这一篇带走什么

- 工具是 agent 接触世界的手，工具设计的好坏直接决定 agent 的能力上限。
- `edit_file` 的「唯一性搜索替换」是关键创新：用「原文必须在全文唯一」这个约束，把不可靠的「改文件」变成确定的操作，连报错都在教模型怎么改对。
- 让模型生成 diff 不可靠，让工具生成 diff 给模型看完全可靠。生成与消费各归其位。
- bash 的正则黑名单是防手滑的闸，不是安全边界。真要面对不可信场景，沙箱得自己补。把这点想清楚，比假装安全重要得多。
- 加一个工具的成本极低：一个类加进默认工具工厂；若要完全替换基础工具集，使用 `Agent(tools=[...])`；若要保留基础工具并追加外部能力，使用 `Agent(extra_tools=[...])`。这是 agent 可扩展性的来源。

下一篇，我们看这只手背后接的是哪个大脑，以及怎么把任意一家的模型接进来、顺手算清楚花了多少钱。
