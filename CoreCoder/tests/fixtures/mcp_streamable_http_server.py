import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("streamable-http-test-server")


@server.tool()
def echo(message: str) -> str:
    """Return the supplied message."""
    return f"echo: {message}"


if __name__ == "__main__":
    server.run(transport="streamable-http", port=int(sys.argv[1]))
