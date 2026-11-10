"""Check discovery and the tool call through the installed Codex app server."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", default="codex.exe")
    parser.add_argument("--account", choices=["codex", "owner"], default="owner")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    server = "pleasanter_probe"
    tool = "mock___whoami"

    with subprocess.Popen([args.codex_bin, "app-server"], cwd=directory,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8") as process:
        def send(message):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            process.stdin.flush()

        def request(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            for line in process.stdout:
                reply = json.loads(line)
                if reply.get("id") != identifier:
                    continue
                if "error" in reply:
                    raise RuntimeError(f"{method} failed ({reply['error']['code']})")
                return reply["result"]
            raise RuntimeError(f"Codex stopped before replying to {method}")

        try:
            request(1, "initialize", {"clientInfo": {"name": "mcp-probe", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
            send({"method": "initialized"})
            thread = request(2, "thread/start", {"cwd": str(directory), "ephemeral": True})
            thread_id = thread["thread"]["id"]
            listed = request(3, "mcpServerStatus/list", {
                "threadId": thread_id, "serverName": server, "detail": "toolsAndAuthOnly",
            })
            status = next((item for item in listed["data"] if item["name"] == server), None)
            if not status:
                raise RuntimeError("Codex did not load the project MCP configuration")
            definition = status["tools"].get(tool)
            if not definition or definition.get("description") != "Return the account identified by the mock API key":
                raise RuntimeError(f"Codex did not discover the mock catalog (auth={status.get('authStatus')})")
            print("PASS Codex discovered the tool description from the mock MCP server")
            result = request(4, "mcpServer/tool/call", {
                "threadId": thread_id, "server": server, "tool": tool, "arguments": {},
            })
            marker = "mock-account:" + args.account
            if result.get("isError") or marker not in json.dumps(result):
                raise RuntimeError("Codex tool call did not return the expected mock account")
            print("PASS Codex received " + marker)
        finally:
            process.terminate()


if __name__ == "__main__":
    main()
