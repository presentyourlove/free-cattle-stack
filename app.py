#!/usr/bin/env python3
"""Tiny always-on service for Cloady (0.5 vCPU / 1GB). Stdlib only, no dependencies.
Routes: GET /health -> 200, POST /webhook -> accepts and logs (extend with real bot logic).
"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "service": "free-cattle-cloady"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path == "/webhook":
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n) if n else b""
            print(f"webhook received: {len(data)} bytes", flush=True)
            body = json.dumps({"accepted": True, "bytes": len(data)}).encode()
            self.send_response(202); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
