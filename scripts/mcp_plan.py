"""Execute an authored JSONL painting plan through real MCP, with inspectable results."""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import timedelta
import json
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters

from mcp.client.stdio import stdio_client


def resolve(value,stored):
    if isinstance(value,dict) and set(value)=={"$ref"}:
        keys=value["$ref"].split("."); result=stored[keys[0]]
        for key in keys[1:]: result=result[int(key)] if isinstance(result,list) else result[key]
        return result
    if isinstance(value,dict): return {k:resolve(v,stored) for k,v in value.items()}
    if isinstance(value,list): return [resolve(v,stored) for v in value]
    return value


async def run(args):
    root=Path(__file__).resolve().parents[1]
    args.evidence.mkdir(parents=True,exist_ok=True)
    params=StdioServerParameters(command=sys.executable,args=["-m","csp_headless.server","--state-dir",str(args.state_dir)])
    stored={}; started=time.monotonic()
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            info=await client.initialize()
            guide=await client.read_resource("paint://guide")
            (args.evidence/"engine-guide.json").write_text(guide.model_dump_json(indent=2),"utf-8")
            (args.evidence/"server.json").write_text(info.model_dump_json(indent=2),"utf-8")
            with args.plan.open("r",encoding="utf-8-sig") as source, (args.evidence/"responses.jsonl").open("w",encoding="utf-8") as journal:
                for line_number,line in enumerate(source,1):
                    if not line.strip(): continue
                    item=json.loads(line)
                    tool=item["tool"]; arguments=resolve(item.get("arguments",{}),stored)
                    before=time.monotonic()
                    result=await client.call_tool(tool,arguments,read_timeout_seconds=timedelta(seconds=600))
                    record={"line":line_number,"tool":tool,"elapsed_seconds":round(time.monotonic()-before,3),
                            "isError":bool(result.isError),"result":result.structuredContent}
                    if result.structuredContent is None:
                        record["text"]=[block.text for block in result.content if block.type=="text"]
                    # Optional "name" keeps evidence file names stable when plan lines are inserted.
                    label="".join(c if c.isalnum() or c in "-_" else "_" for c in str(item.get("name") or tool))[:64]
                    images=[block for block in result.content if block.type=="image"]
                    for index,block in enumerate(images):
                        # One image (a snapshot) is saved as name.png; several get -0, -1 ... suffixes.
                        suffix="" if len(images)==1 else f"-{index}"
                        path=args.evidence/(f"{label}{suffix}.png" if item.get("name") else f"{line_number:03}-{tool}{suffix}.png")
                        path.write_bytes(base64.b64decode(block.data))
                        record.setdefault("images",[]).append(str(path.resolve()))
                    journal.write(json.dumps(record,ensure_ascii=False)+"\n"); journal.flush()
                    if result.isError: raise RuntimeError(f"Plan line {line_number} failed: {record}")
                    if item.get("store"): stored[item["store"]]=result.structuredContent
                    print(json.dumps({k:record[k] for k in ("line","tool","elapsed_seconds","isError")}),flush=True)
    (args.evidence/"summary.json").write_text(json.dumps({"elapsed_seconds":round(time.monotonic()-started,3),"stored":stored},ensure_ascii=False,indent=2),"utf-8")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("plan",type=Path)
    parser.add_argument("--state-dir",type=Path,required=True)
    parser.add_argument("--evidence",type=Path,required=True)
    asyncio.run(run(parser.parse_args()))


if __name__=="__main__": main()
