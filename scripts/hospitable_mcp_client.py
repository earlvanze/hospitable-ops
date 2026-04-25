#!/usr/bin/env python3
"""Hospitable MCP Client - Wrapper for https://mcp.hospitable.com/mcp"""

import subprocess
import json
import sys
from typing import Any, Optional

MCPORTER_CONFIG = "/home/umbrel/.openclaw/workspace/config/mcporter.json"
HOSPITABLE_MCP_URL = "https://mcp.hospitable.com/mcp"


def call_mcp_tool(tool_name: str, args: Optional[dict] = None, dry_run: bool = False) -> dict[str, Any]:
    """Call a Hospitable MCP tool via mcporter.
    
    Args:
        tool_name: Tool name (e.g., "get_reservation", "block_dates")
        args: Tool arguments as dict
        dry_run: If True, print command instead of executing
    
    Returns:
        Tool response as dict
    """
    cmd = ["mcporter", "call", f"hospitable.{tool_name}"]
    
    if args:
        for key, value in args.items():
            cmd.append(f"{key}={json.dumps(value) if isinstance(value, (dict, list)) else value}")
    
    if dry_run:
        print(f"Would execute: {' '.join(cmd)}")
        return {"status": "dry_run", "command": " ".join(cmd)}
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"error": result.stderr, "stdout": result.stdout}
        return json.loads(result.stdout) if result.stdout else {"status": "success"}
    except subprocess.TimeoutExpired:
        return {"error": "MCP call timed out after 30s"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}", "stdout": result.stdout}


def list_tools() -> list[str]:
    """List available Hospitable MCP tools."""
    result = subprocess.run(
        ["mcporter", "list", "hospitable", "--schema"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        print(f"Error listing tools: {result.stderr}", file=sys.stderr)
        return []
    
    tools = []
    for line in result.stdout.split('\n'):
        if line.strip().startswith('- '):
            tools.append(line.strip()[2:].split(' ')[0])
    return tools


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Hospitable MCP Client")
    parser.add_argument("action", choices=["list", "call", "test"], help="Action to perform")
    parser.add_argument("--tool", help="Tool name for call action")
    parser.add_argument("--args", help="JSON arguments for tool call")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing")
    args = parser.parse_args()
    
    if args.action == "list":
        tools = list_tools()
        print("Available Hospitable MCP tools:")
        for tool in tools:
            print(f"  - {tool}")
    
    elif args.action == "call":
        if not args.tool:
            print("Error: --tool required for call action", file=sys.stderr)
            sys.exit(1)
        tool_args = json.loads(args.args) if args.args else {}
        result = call_mcp_tool(args.tool, tool_args, args.dry_run)
        print(json.dumps(result, indent=2))
    
    elif args.action == "test":
        print("Testing Hospitable MCP connection...")
        result = call_mcp_tool("list_properties", {}, args.dry_run)
        if "error" in result:
            print(f"Test failed: {result['error']}")
            sys.exit(1)
        print(f"Test passed: {len(result.get('properties', []))} properties found")


if __name__ == "__main__":
    main()
