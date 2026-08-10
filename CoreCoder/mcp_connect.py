import sys
import asyncio

def log(msg):
    with open("mcp_run.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

async def main():
    log("=== connecting to firecrawl MCP ===")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client("https://mcp.firecrawl.dev/v2/mcp") as (read, write):
        log("connected")
        async with ClientSession(read, write) as session:
            await session.initialize()
            log("initialized")
            result = await session.list_tools()
            log("tools found: %d" % len(result.tools))
            for t in result.tools:
                log("  - %s : %s" % (t.name, (t.description or "")[:100]))
            # save tool list to file
            with open("mcp_tools.txt", "w", encoding="utf-8") as f:
                for t in result.tools:
                    f.write("%s\t%s\n" % (t.name, t.description or ""))

asyncio.run(main())
