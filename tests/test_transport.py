import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from PIL import Image

from csp_headless.engine import OPERATIONS


def test_real_mcp_session_and_failures(tmp_path):
    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=["-m","csp_headless.server","--state-dir",str(tmp_path/"mcp-state")])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                catalog=await session.list_tools()
                assert {t.name for t in catalog.tools}==set(OPERATIONS)
                resources=await session.list_resources()
                assert any(str(r.uri)=="paint://guide" for r in resources.resources)
                response=await session.call_tool("paint_document",{"action":"create","width":64,"height":64})
                assert not response.isError
                response=await session.call_tool("paint_strokes",{"strokes":[{"points":[[5,5,.1],[50,50,1]],"color":"#FF0000"}]})
                assert not response.isError
                snapshot=await session.call_tool("paint_snapshot",{})
                assert not snapshot.isError and any(c.type=="image" for c in snapshot.content)
                assert snapshot.structuredContent["size"]==[64,64]
                failed=await session.call_tool("paint_batch",{"actions":[
                    {"tool":"paint_fill","arguments":{"color":"#0000FF"}},
                    {"tool":"paint_fill","arguments":{"color":"not-a-color"}}]})
                assert failed.isError and failed.structuredContent["scene_mutations_rolled_back"]
                malformed=await session.call_tool("paint_strokes",{"strokes":[{"points":[]} ]})
                assert malformed.isError
                output=tmp_path/"mcp.png"
                exported=await session.call_tool("paint_document",{"action":"export","path":str(output)})
                assert not exported.isError
                with Image.open(output) as image: assert image.size==(64,64)
    asyncio.run(scenario())


def test_real_http_mcp_without_window(tmp_path):
    import socket
    import subprocess
    import time
    with socket.socket() as probe:
        probe.bind(("127.0.0.1",0)); port=probe.getsockname()[1]
    process=subprocess.Popen([sys.executable,"-m","csp_headless.server","--transport","http","--port",str(port),
        "--state-dir",str(tmp_path/"http-state")],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        deadline=time.monotonic()+25
        while True:
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2): break
            except OSError:
                if process.poll() is not None: raise AssertionError(process.stderr.read().decode())
                if time.monotonic()>deadline: raise AssertionError("HTTP MCP startup timeout")
                time.sleep(.1)
        async def scenario():
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    created=await session.call_tool("paint_document",{"action":"create","width":32,"height":32})
                    assert not created.isError
                    result=await session.call_tool("paint_fill",{"color":"#123456"})
                    assert not result.isError
                    sample=await session.call_tool("paint_sample",{"points":[[16,16]]})
                    assert sample.structuredContent["samples"][0]["rgba"]==[18,52,86,255]
        asyncio.run(scenario())
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        process.stderr.close()
