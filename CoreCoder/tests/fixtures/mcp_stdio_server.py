from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

server = MCPServer("test-server")


@server.tool()
def echo(message: str) -> str:
    """Return the supplied message."""
    return f"echo: {message}"


@server.tool()
def long_output() -> str:
    """Return enough output to exercise client-side context protection."""
    return "a" * 16_000


@server.tool()
def tool_error() -> str:
    """Return a server execution error that the agent can correct."""
    raise ValueError("The server rejected this request.")


@server.tool()
async def notify_tools_changed(context: Context) -> str:
    """Send a tools-list change notification without modifying this snapshot."""
    await context.session.send_tool_list_changed()
    return "notification sent"


if __name__ == "__main__":
    server.run(transport="stdio")
