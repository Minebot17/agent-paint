"""Add this engine as its own Codex STDIO server; preserve all other configuration."""
from __future__ import annotations

import argparse
from pathlib import Path
import tomllib


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--backup",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    executable=root/".venv"/"Scripts"/"python.exe"
    if not executable.is_file(): raise SystemExit("Create the Windows venv before registration")
    raw=args.config.read_bytes()
    data=tomllib.loads(raw.decode("utf-8-sig"))
    desired={"command":executable.as_posix(),"args":["-m","csp_headless.server","--state-dir",(root/".state").as_posix()]}
    existing=data.get("mcp_servers",{}).get("agent_paint")
    if existing:
        if existing==desired: print("agent_paint already configured"); return
        raise SystemExit("agent_paint already exists with a different configuration; no changes made")
    import json
    block="\n\n[mcp_servers.agent_paint]\ncommand = "+json.dumps(desired["command"])+"\nargs = "+json.dumps(desired["args"])+"\n"
    updated=raw.decode("utf-8-sig").rstrip()+block
    parsed=tomllib.loads(updated)
    assert parsed["mcp_servers"]["agent_paint"]==desired
    del parsed["mcp_servers"]["agent_paint"]
    assert parsed==data,"Registration must not change unrelated settings"
    args.backup.parent.mkdir(parents=True,exist_ok=True)
    with args.backup.open("xb") as file: file.write(raw)
    # Replacement is atomic. Keep the complete config backup outside distributable outputs.
    from csp_headless.storage import atomic_write
    atomic_write(args.config,lambda stream:stream.write(updated.encode("utf-8")))
    print("Added agent_paint; all existing MCP servers preserved")


if __name__=="__main__": main()
