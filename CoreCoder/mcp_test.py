import sys

def log(msg):
    with open("mcp_test.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log("start")
try:
    from mcp.client.streamable_http import streamablehttp_client
    log("import streamablehttp_client ok")
except Exception as e:
    log("import failed: %r" % e)
