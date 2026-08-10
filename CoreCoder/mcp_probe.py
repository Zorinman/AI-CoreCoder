import sys

def log(msg):
    with open("mcp_test.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log("=== start ===")
try:
    import mcp
    log("mcp file: %s" % mcp.__file__)
except Exception as e:
    log("mcp import failed: %r" % e)

try:
    import mcp.client.streamable_http as m
    log("streamable_http names: %s" % [x for x in dir(m) if not x.startswith('_')])
except Exception as e:
    log("streamable_http import failed: %r" % e)

try:
    from mcp.client.stdio import stdio_client
    log("stdio_client ok")
except Exception as e:
    log("stdio_client import failed: %r" % e)
