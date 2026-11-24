#!/usr/bin/env python3
"""Wake the Simular box through its MCP server, using a personal API key.

Used by the keepalive GitHub Actions workflow so the box never reaches
14 consecutive days asleep (free boxes are deleted after that).
Reads SIMULAR_API_KEY from the environment (a GitHub Actions secret).
An empty variable prints a notice and exits 0, so the rest of the
keepalive run still reports. A present-but-rejected key is a real
failure and exits 1.
"""
import json
import os
import sys
import urllib.request
import urllib.error

ENDPOINT = "https://mcp.box.simular.ai/mcp"


def post(payload, key, session=None, timeout=180):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(ENDPOINT, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("Authorization", f"Bearer {key}")
    if session:
        req.add_header("Mcp-Session-Id", session)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.headers.get("Mcp-Session-Id"), resp.headers.get_content_type(), resp.read()


def extract_result(content_type, raw, want_id):
    if content_type == "application/json":
        msgs = [json.loads(raw)]
    else:  # text/event-stream: one JSON message per data: line
        msgs = []
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                try:
                    msgs.append(json.loads(line[5:].strip()))
                except Exception:
                    pass
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == want_id:
            if "error" in m:
                raise RuntimeError(f"JSON-RPC error: {m['error']}")
            return m.get("result")
    raise RuntimeError("no matching JSON-RPC result in response")


def main():
    key = os.environ.get("SIMULAR_API_KEY", "").strip()
    if not key:
        print("SIMULAR_API_KEY not set; skipping Simular wake.")
        return 0
    try:
        session, ct, raw = post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "free-cattle-keepalive", "version": "1"}},
        }, key)
        extract_result(ct, raw, 1)
        post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, key, session)
        _s, ct, raw = post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "box_ensure", "arguments": {"wait": True}},
        }, key, session)
        result = extract_result(ct, raw, 2)
    except urllib.error.HTTPError as e:
        print(f"Simular MCP returned HTTP {e.code}; key rejected or server error.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Simular wake failed: {e}", file=sys.stderr)
        return 1
    if isinstance(result, dict) and result.get("isError"):
        print(f"box_ensure returned an error: {result}", file=sys.stderr)
        return 1
    text = ""
    if isinstance(result, dict):
        for part in result.get("content", []):
            if part.get("type") == "text":
                text = part["text"]
    print(f"box_ensure ok: {text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
