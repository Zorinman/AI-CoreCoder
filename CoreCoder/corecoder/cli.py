"""Interactive REPL - the user-facing terminal interface."""

import sys
import os
import argparse

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from .agent import Agent
from .config import Config, ConfigError
from .fallback import ModelChain
from .session import save_session, load_session, list_sessions
from . import __version__

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="ZorinHelper AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Primary model name (overrides $CORECODER_PRIMARY_MODEL)")
    p.add_argument("--base-url", help="Primary API base URL (overrides $CORECODER_PRIMARY_BASE_URL)")
    p.add_argument("--api-key", help="Primary API key (overrides $CORECODER_PRIMARY_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"ZorinHelper {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()
    try:
        config = Config.from_env({
            "model": args.model,
            "base_url": args.base_url,
            "api_key": args.api_key,
        })
    except ConfigError as exc:
        console.print(f"[red]Invalid configuration: {exc}[/red]")
        sys.exit(2)

    chain = ModelChain.from_config(config)
    agent = Agent(llm=chain, max_context_tokens=config.max_context_tokens)
    model_override: str | None = None

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, saved_override = loaded
            # restore the model from the saved session unless overridden by CLI
            if saved_override and not args.model:
                agent.switch_model(saved_override)
                model_override = saved_override
            console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config, model_override)


def _run_once(agent: Agent, prompt: str):
    """Non-interactive: run one prompt and exit."""
    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    try:
        agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)
    print()


def _repl(agent: Agent, config: Config, model_override: str | None = None):
    """Interactive read-eval-print loop."""
    console.print(Panel(
        f"[bold]ZorinHelper[/bold] v{__version__}\n"
        f"{_startup_model_summary(config)}"
        + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
        border_style="blue",
    ))

    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            console.print(line)
            continue
        if user_input == "/budget" or user_input.startswith("/budget "):
            raw_limit = user_input[7:].strip() if user_input.startswith("/budget ") else ""
            if not raw_limit:
                current = agent.llm.budget.limit_usd
                console.print(
                    f"Budget: [cyan]${current:.6f}[/cyan]" if current is not None else "Budget: [dim]unlimited[/dim]"
                )
                continue
            try:
                new_limit = float(raw_limit)
                agent.llm.set_budget_limit(new_limit, only_increase=True)
            except (TypeError, ValueError) as exc:
                console.print(f"[red]Invalid budget: {exc}[/red]")
            else:
                console.print(f"Budget limit set to [cyan]${new_limit:.6f}[/cyan]")
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
            if new_model:
                agent.switch_model(new_model)
                model_override = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{agent.llm.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = save_session(agent.messages, model_override)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: corecoder -r {sid}")
            continue
        if user_input == "/diff":
            from .tools.edit import _changed_files
            if not _changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(_changed_files)}):[/bold]")
                for f in sorted(_changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            continue

        # an unknown /command shouldn't be sent to the model as a prompt
        if user_input.startswith("/"):
            console.print(f"[yellow]Unknown command: {user_input.split()[0]} (try /help)[/yellow]")
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                print()  # newline after streamed tokens
                for line in response.splitlines():
                    if line.startswith("当前使用模型已切换为备用模型 "):
                        console.print(f"[yellow]{line}[/yellow]")
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    console.print(Panel(
        "[bold]Commands:[/bold]\n"
        "  /help          Show this help\n"
        "  /reset         Clear conversation history\n"
        "  /model         Show current model\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /tokens        Show token usage\n"
        "  /budget        Show budget limit\n"
        "  /budget <usd>  Increase budget limit\n"
        "  /compact       Compress conversation context\n"
        "  /diff          Show files modified this session\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  quit           Exit ZorinHelper\n"
        "\n"
        "[bold]Input:[/bold]\n"
        "  Enter          Submit message\n"
        "  Esc+Enter      Insert newline (for pasting code)",
        title="ZorinHelper Help",
        border_style="dim",
    ))


def _startup_model_summary(config: Config) -> str:
    """Format only the configured model names and base URLs for the startup panel."""
    lines = [_startup_model_line("Model", config.primary.model, config.primary.base_url)]
    for index, fallback in enumerate(config.fallbacks, start=1):
        lines.append(_startup_model_line(f"Fallback {index}", fallback.model, fallback.base_url))
    return "\n".join(lines)


def _startup_model_line(label: str, model: str, base_url: str | None) -> str:
    base = f"  Base: [dim]{base_url}[/dim]" if base_url else ""
    return f"{label}: [cyan]{model}[/cyan]{base}"


def _brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
