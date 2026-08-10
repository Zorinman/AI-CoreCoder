"""Core agent loop.

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
import inspect
import json

from .context import ContextManager
from .fallback import BudgetExceededError, ModelChain
from .llm import LLM
from .prompt import system_prompt
from .tools.agent import AgentTool
from .tools.base import Tool
from .tools.bash import BashTool
from .tools.edit import EditFileTool
from .tools.glob_tool import GlobTool
from .tools.grep import GrepTool
from .tools.read import ReadFileTool
from .tools.runtime import ToolRuntimeState
from .tools.write import WriteFileTool


class Agent:
    def __init__(
        self,
        llm: LLM | ModelChain,
        tools: list[Tool] | None = None,
        extra_tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        model_chain: ModelChain | None = None,
        _include_agent_tool: bool = True,
    ):
        self.llm = model_chain if model_chain is not None else llm
        self.model_chain = self.llm if isinstance(self.llm, ModelChain) else model_chain
        self._runtime_state = ToolRuntimeState()
        self.extra_tools = list(extra_tools or [])
        base_tools = tools if tools is not None else self._create_default_tools()
        if not _include_agent_tool:
            base_tools = [tool for tool in base_tools if tool.name != "agent"]
        self.tools = [*base_tools, *self.extra_tools]
        self._validate_tool_names()
        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self._system = system_prompt(self.tools)
        self.startup_context_tokens = self._estimate_startup_context_tokens()
        if self.startup_context_tokens > max_context_tokens:
            raise ValueError(
                f"System prompt and tool schemas require {self.startup_context_tokens} tokens, "
                f"exceeding the {max_context_tokens}-token context window."
            )
        self.startup_context_warning = self.startup_context_tokens > max_context_tokens * 0.25

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    def _create_default_tools(self) -> list[Tool]:
        state = self._runtime_state
        return [
            BashTool(state),
            ReadFileTool(),
            WriteFileTool(state),
            EditFileTool(state),
            GlobTool(),
            GrepTool(),
            AgentTool(),
        ]

    def _validate_tool_names(self) -> None:
        names = [tool.name for tool in self.tools]
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        if duplicate_names:
            joined_names = ", ".join(duplicate_names)
            raise ValueError(f"Tool names must be unique; duplicates: {joined_names}")

    def _estimate_startup_context_tokens(self) -> int:
        schema_json = json.dumps(self._tool_schemas(), ensure_ascii=False, sort_keys=True)
        return (len(self._system) + len(schema_json)) // 3

    @property
    def changed_files(self) -> frozenset[str]:
        """Return the files changed by this Agent as a read-only snapshot."""
        return frozenset(self._runtime_state.changed_files)

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)
        fallback_messages: list[str] = []

        for _ in range(self.max_rounds):
            try:
                if hasattr(self.llm, "can_start_request") and not self.llm.can_start_request():
                    return (
                        f"Estimated cost ${self.llm.estimated_cost:.6f} exceeds the hard budget "
                        f"of ${self.llm.budget.limit_usd:.6f}. Use /budget <amount> to increase it."
                    )
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

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            try:
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result = self._exec_tool(tc)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    # parallel execution for multiple tool calls
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, result in zip(resp.tool_calls, results):
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
            except KeyboardInterrupt:
                # Ctrl+C mid-execution would leave the assistant tool_calls
                # message without replies, poisoning the next request; backfill
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
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
        except Exception as e:  # noqa: BLE001 - tool boundaries return execution failures to the model.
            return f"Error executing {tc.name}: {e}"

    def _tool_is_safe(self, tc) -> bool:
        """Whether tc's tool declared itself concurrency-safe. Unknown tools
        (not found in _tool_by_name) are treated as unsafe, same as the
        Tool base class default."""
        tool = self._tool_by_name.get(tc.name)
        return bool(tool is not None and getattr(tool, "concurrency_safe", False))

    def _segment_tool_calls(self, tool_calls) -> list[tuple[bool, list]]:
        """Split tool_calls into (is_safe, calls) runs, preserving original
        order. Consecutive safe calls share one concurrent segment;
        consecutive unsafe calls share one serial segment. This keeps the
        causal order of unsafe (side-effecting) calls intact while still
        letting runs of safe calls execute in parallel."""
        segments: list[tuple[bool, list]] = []
        for tc in tool_calls:
            safe = self._tool_is_safe(tc)
            if segments and segments[-1][0] == safe:
                segments[-1][1].append(tc)
            else:
                segments.append((safe, [tc]))
        return segments

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls, executing consecutive concurrency-safe
        calls in parallel and everything else serially, in original order.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, segment them by safety
        and run each segment accordingly.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        results: list[str] = []
        for safe, group in self._segment_tool_calls(tool_calls):
            if safe and len(group) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(self._exec_tool, tc) for tc in group]
                    results.extend(f.result() for f in futures)
            else:
                results.extend(self._exec_tool(tc) for tc in group)
        return results

    def _answer_pending_tool_calls(self, tool_calls):
        """Backfill a tool reply for every call that didn't get one.

        OpenAI-compatible APIs reject a request where an assistant message has
        tool_calls without a matching tool reply for each id, so this keeps the
        history valid when execution is interrupted partway through.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted]",
                })

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()

    def switch_model(self, model: str) -> None:
        """Explicit model selection also clears a fallback circuit breaker."""
        if hasattr(self.llm, "set_model"):
            self.llm.set_model(model)
        else:
            self.llm.model = model
