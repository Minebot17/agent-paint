"""Run JSONL plans without MCP or any application window."""
import argparse
import json
from pathlib import Path
import sys

from .engine import Engine, Picture


def main():
    parser=argparse.ArgumentParser(description="Synchronous Agent Paint JSONL runner")
    parser.add_argument("plan",type=Path,help="UTF-8 JSONL: one {tool,arguments} per line; - reads stdin")
    parser.add_argument("--state-dir",type=Path)
    args=parser.parse_args()
    engine=Engine(args.state_dir)
    stream=sys.stdin if str(args.plan)=="-" else args.plan.open("r",encoding="utf-8-sig")
    try:
        for number,line in enumerate(stream,1):
            if not line.strip(): continue
            try:
                item=json.loads(line)
                if set(item)-{"tool","arguments"}: raise ValueError("Expected {tool,arguments}")
                result=engine.call(item["tool"],**item.get("arguments",{}))
                if isinstance(result,Picture): result=result.info
                print(json.dumps({"line":number,"result":result},ensure_ascii=True),flush=True)
            except Exception as error:
                print(json.dumps({"line":number,"error":str(error)},ensure_ascii=True),flush=True)
                return 1
    finally:
        if stream is not sys.stdin: stream.close()
    return 0


if __name__=="__main__": sys.exit(main())
