# 接入任意大模型，顺便把钱算清楚

前两篇讨论循环和工具，也就是 agent 的执行机制。本篇转向模型接口：如何接入模型、如何处理流式输出、如何应对 provider 的瞬时故障，以及一个常被教程忽略却会直接影响使用的问题：这一轮调用花了多少钱。

对应的文件是 `corecoder/llm.py`，300 行，是整个项目最大的单文件。它大，是因为它替你扛下了和真实 API 打交道时所有不优雅的部分。

## 一个赌注：大家都长得像 OpenAI

`llm.py` 开头的注释把整个设计的赌注讲明白了：

> 既然大多数 provider（DeepSeek、Qwen、Kimi、GLM、Ollama 等）都暴露了 OpenAI 兼容的接口，我们就直接用 openai SDK。换 provider 主要是换主模型配置里的模型名、密钥和 Base URL。

这一选择在 2026 年已相当稳妥。OpenAI 的 `/v1/chat/completions` 接口已成为事实标准，国内外多数模型服务要么原生兼容，要么提供兼容端点。因此，CoreCoder 的主力 `LLM` 类本质上是对 openai 官方 SDK 的轻量封装。若要从 OpenAI 切换到 DeepSeek，无需修改代码，只需调整 `.env` 中的主模型配置：

```bash
CORECODER_PRIMARY_MODEL=deepseek-chat
CORECODER_PRIMARY_API_KEY=sk-...
CORECODER_PRIMARY_BASE_URL=https://api.deepseek.com/v1
```

这个「一套接口接住所有人」的选择，是 CoreCoder 能这么小的重要原因之一。它没有为每家 provider 写一个 adapter，因为它赌大家都会向 OpenAI 的形状靠拢。

那不兼容的怎么办？比如 AWS Bedrock、Google Vertex 这些。`llm.py` 末尾有一个 `LiteLLM` 子类兜底，后面会讲。先看主路径。

## 流式输出，比你想的麻烦一点

`LLM.chat()` 把消息发出去，开了 `stream=True`，然后一块一块地收。文本好办，来一块拼一块。真正麻烦的是工具调用，因为工具调用的参数也是流式吐出来的，一个调用的 JSON 参数会被切成好几个碎片，分散在多个 chunk 里到达，你得自己把它们按调用的次序缝回去。

CoreCoder 用一个以 index 为键的字典来缝：

```python
tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}

for chunk in stream:
    # ...
    if delta.tool_calls:
        for tc_delta in delta.tool_calls:
            idx = tc_delta.index
            if idx not in tc_map:
                tc_map[idx] = {"id": "", "name": "", "args": ""}
            if tc_delta.id:
                tc_map[idx]["id"] = tc_delta.id
            if tc_delta.function:
                if tc_delta.function.name:
                    tc_map[idx]["name"] = tc_delta.function.name
                if tc_delta.function.arguments:
                    tc_map[idx]["args"] += tc_delta.function.arguments
```

注意那个 `+=`。参数字符串是累加上去的，因为它一次只到一截。等流收完，再把每个调用累积的参数字符串 `json.loads` 成真正的字典：

```python
for idx in sorted(tc_map):
    raw = tc_map[idx]
    try:
        args = json.loads(raw["args"])
    except (json.JSONDecodeError, KeyError):
        args = {}
    parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))
```

这里还做了一层防御：如果累积后的参数不是合法 JSON，例如模型只返回半截内容或参数格式损坏，流程不会直接中断，而是退化为空字典 `{}`。随后由上一篇介绍的参数校验向模型反馈「参数不正确」。流式解析必须能够优雅处理坏数据，因为流随时可能携带不完整内容。

文本部分还支持一个 `on_token` 回调，每收到一截文本就喊一声，CLI 拿这个回调实现「打字机」效果，让你看着字一个个冒出来，而不是干等几十秒蹦出一整段。

## token 是怎么数出来的

要算钱，先得知道每次调用花了多少 token。CoreCoder 不自己估，它问 provider 要准数。办法是在请求里加一个 `stream_options`：

```python
params["stream_options"] = {"include_usage": True}
```

加了这个，provider 会在流的最后一个 chunk 里带回 `usage`，里头有 `prompt_tokens` 和 `completion_tokens`。代码在循环里接住它：

```python
if chunk.usage:
    # some providers send usage with null fields; coerce to 0 so the
    # running totals below don't blow up on int + None
    prompt_tok = chunk.usage.prompt_tokens or 0
    completion_tok = chunk.usage.completion_tokens or 0
```

那个 `or 0` 不是多余的。有些 provider 会把 `usage` 发回来但字段是 `null`，要是直接拿去和累计值相加，`int + None` 会抛异常，把整个会话搞挂。一句 `or 0` 把这种脏数据压平。这又是一个「真实 API 不会按文档那么干净」的例子，写包装层的人得替上层把这些坑都垫平。

数出来的 token 累加进 `total_prompt_tokens` 和 `total_completion_tokens`，CLI 的 `/tokens` 命令随时能查。

## provider 抽风了，自己扛

主循环 `agent.py` 不包含重试逻辑，因为这项职责被下放到模型接口层。`_call_with_retry` 专门处理瞬时故障：

```python
def _call_with_retry(self, params: dict, max_retries: int = 3):
    """Retry on transient errors with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return self.client.chat.completions.create(**params)
        except (RateLimitError, APITimeoutError, APIConnectionError):
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            time.sleep(wait)
        except APIError as e:
            # retry 5xx server errors but not 4xx; base APIError has no
            # status_code so read it defensively
            status_code = getattr(e, "status_code", None)
            if status_code and status_code >= 500 and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
```

逻辑是经典的指数退避：限流、超时、连接错误这类一看就是瞬时的故障，等 1 秒、2 秒、4 秒重试，最多三次还不行就放弃抛出去。服务端 5xx 也重试，但客户端 4xx（参数错、鉴权失败这种）绝不重试，因为重试一百次结果都一样，只是白等。

这段重试逻辑值得单独说明。项目规模小，并不意味着可以忽略重试；这里甚至考虑到基类 `APIError` 可能没有 `status_code` 属性，因此使用 `getattr` 防御。不同版本 SDK 的异常层级并不完全一致，直接访问属性可能导致新的异常。

CoreCoder 现在也实现了这两道保护：模型耗尽自身重试后，`ModelChain` 会按 `Config` 配置的顺序尝试备用级别；进程级硬预算会在估算成本超过上限后拒绝新的请求。降级选择保持 provider 无关，价格仍由显式价目表提供；未列入价目表的模型无法估算成本，因此采用放行策略而不编造价格。

还有个容易被忽略的协调细节。`stream_options` 是 OpenAI 的扩展，有些 provider 不认，会回一个 400。CoreCoder 的处理是：

```python
try:
    stream = self._call_with_retry(params)
except BadRequestError:
    params.pop("stream_options", None)
    stream = self._call_with_retry(params)
```

捕获 `BadRequestError`（400），把 `stream_options` 去掉再试一次。注释里特意说明了，为什么只在这里 fallback、不在 `_call_with_retry` 里一起处理：因为 `_call_with_retry` 已经把瞬时错误重试过了，如果把这个「去参数重试」也塞进去，会让重试次数翻倍。两种重试，一种针对「参数被拒」、一种针对「瞬时故障」，原因不同，就分开放，别耦合。这种对「为什么这段代码在这里而不在那里」的交代，是读源码时最值钱的东西。

## 不兼容 OpenAI 的，交给 LiteLLM

`LiteLLM` 子类用于接入不提供 OpenAI 兼容接口的 provider。它继承 `LLM`，但跳过父类中创建 openai client 的步骤，改由 `litellm` 库路由请求；该库支持上百家 provider：

```python
class LiteLLM(LLM):
    def _call_with_retry(self, params, max_retries=3):
        import litellm
        params["drop_params"] = True   # 不支持的参数自动丢掉，别报错
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url
        # ...同样的指数退避...
```

设置 `CORECODER_PRIMARY_BACKEND=litellm` 后，就可以使用 LiteLLM 的模型标识，例如 `anthropic/claude-3-haiku`、`bedrock/anthropic.claude-v2`、`vertex_ai/gemini-pro`。`PROVIDER` 只是供应商标识，不决定客户端类型。`drop_params=True` 会在 provider 不支持某个参数时自动忽略该参数，避免因参数差异直接报错。大多数场景使用 OpenAI 兼容路径即可；LiteLLM 为差异较大的 provider 提供统一入口。

两条后端并不各自再写一遍流式解析。它们负责按各自协议拿到 `stream`，之后都调用继承来的 `_decode_stream()`：

```python
response = self._decode_stream(stream, on_token)
self._record_usage(response.prompt_tokens, response.completion_tokens)
return response
```

这一个解码器统一处理文本回调、按调用 index 拼接工具调用、无效 JSON 回退为空参数，以及最终 chunk 的 token 用量。它还容忍空 `choices`、缺失的 `delta` 或 `function` 字段，不让某个 provider 的可选字段缺失拖垮整次响应。这样 OpenAI 兼容后端与 LiteLLM 后端只在请求参数和重试策略上不同，流式响应的语义保持一致；修一次解析边角，两边同时生效。

## 把钱算出来

拿到 token 用量后，成本计算就是按照价目表换算。`llm.py` 中维护了一张以百万 token 为单位的价格表，分别记录输入和输出单价：

```python
_PRICING = {
    "gpt-4.1": (2, 8),
    "deepseek-chat": (0.27, 1.10),
    "claude-sonnet-4-6": (3, 15),
    "kimi-k2.5": (0.6, 3),
    # ...
}
```

`estimated_cost` 这个 property 拿累计 token 乘上对应单价：

```python
@property
def estimated_cost(self) -> float | None:
    pricing = _PRICING.get(self.model)
    if not pricing:
        return None
    input_rate, output_rate = pricing
    return (
        self.total_prompt_tokens * input_rate / 1_000_000
        + self.total_completion_tokens * output_rate / 1_000_000
    )
```

注意返回类型是 `float | None`。表里没有的模型，它不瞎猜，老老实实返回 `None`，CLI 那边看到 `None` 就不显示价格，而不是编一个数字骗你。这是个小但重要的诚实：宁可说「我不知道」，也不给你一个看着精确、实则瞎编的成本。CLI 的 `/tokens` 命令把它显示出来：

```
Tokens: 12043 prompt + 3201 completion = 15244 total  (~$0.0621)
```

当然，这只是估算。价目表会随时间变化，缓存折扣和批量定价等因素也未纳入计算。不过，对于判断一次操作的大致成本，它已经足够。能够及时看到成本的 agent，与只能在账单到来后再追溯成本的 agent，使用体验截然不同。

预算准入不直接读取 `_PRICING` 这张私有表，而是通过 LLM 公开的 `has_known_cost` 能力判断当前模型是否有可用定价数据。已知价格的模型会在发起新请求前检查硬预算；未知价格的模型按放行策略处理，同时仍会累计实际拿到的 token 用量。

## 配置从哪来

最后串一下 `config.py`。它不再把一堆扁平变量拼成一个模型，而是把主模型和每一级备用模型都解析成同一种不可变 `ModelConfig`：

```python
CORECODER_PRIMARY_MODEL=deepseek-chat
CORECODER_PRIMARY_API_KEY=sk-...
CORECODER_PRIMARY_BACKEND=openai_compatible
CORECODER_PRIMARY_PROVIDER=deepseek
CORECODER_PRIMARY_BASE_URL=https://api.deepseek.com/v1
CORECODER_PRIMARY_TEMPERATURE=0
CORECODER_PRIMARY_MAX_TOKENS=4096

# 可选的第一个备用模型；启用时 MODEL 与 API_KEY 同样必填。
CORECODER_FALLBACK_1_MODEL=anthropic/claude-haiku-4-5
CORECODER_FALLBACK_1_API_KEY=sk-...
CORECODER_FALLBACK_1_BACKEND=litellm
CORECODER_FALLBACK_1_PROVIDER=anthropic
CORECODER_FALLBACK_1_TEMPERATURE=0
CORECODER_FALLBACK_1_MAX_TOKENS=4096
```

备用编号必须从 1 连续递增；每一级的 `MODEL` 与 `API_KEY` 必填，其他字段也属于该级自己的配置，`BASE_URL` 可以省略。`BACKEND` 只能是 `openai_compatible` 或 `litellm`，`PROVIDER` 只是非空的供应商标识。CLI 的 `--model`、`--api-key`、`--base-url` 只覆盖主模型对应字段；其后才是进程环境变量、当前目录 `.env` 和默认生成参数。旧的 `OPENAI_*`、`CORECODER_MODEL` 等变量会明确报错并给出迁移目标，不再悄悄兼容。`ModelConfig` 和外层 `Config` 都是不可变快照：前者描述单个模型，后者保存主模型、备用链、上下文上限和预算上限。`ModelChain.from_config()` 用同一条路径构造主备模型，因而温度、最大输出等参数不会在降级时丢失；运行时的当前级别、熔断、预算和模型名覆盖由模型链与 `BudgetState` 保存，不回写启动配置。`.env.example` 列出全部正式字段，备用模型块默认注释，复制模板后不会意外启用不完整的备用级别。

