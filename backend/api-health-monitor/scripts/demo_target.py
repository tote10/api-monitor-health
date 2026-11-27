"""Local, deterministic health target and webhook receiver."""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, DemoHandler)
        self.mode = "ok"
        self.events = {}


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, payload=None):
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/events":
            self.reply(200, list(self.server.events.values()))
        elif self.path == "/workflow/profile":
            if self.headers.get("Authorization") != "Bearer demo-session":
                self.reply(401)
            elif self.server.mode == "down":
                self.reply(503)
            else:
                self.reply(200, {"account_active": True})
        elif self.path == "/workflow/finish":
            self.reply(204)
        elif self.path == "/health":
            mode = self.server.mode
            if mode == "slow":
                time.sleep(2)
            self.reply(503 if mode == "down" else 200, {"mode": mode})
        else:
            self.reply(404)

    def do_POST(self):
        if self.path == "/workflow/login":
            self.reply(200, {"data": {"token": "demo-session"}})
        elif self.path.startswith("/mode/"):
            mode = self.path.removeprefix("/mode/")
            if mode not in {"ok", "down", "slow"}:
                self.reply(400)
                return
            self.server.mode = mode
            self.reply(200, {"mode": mode})
        elif self.path == "/webhook":
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                self.reply(400)
                return
            event = json.loads(self.rfile.read(size))
            key = self.headers.get("Idempotency-Key", event["delivery_id"])
            self.server.events.setdefault(key, event)
            self.reply(204)
        else:
            self.reply(404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    with DemoServer(("127.0.0.1", arguments.port)) as server:
        print(f"Demo target on http://127.0.0.1:{arguments.port}", flush=True)
        server.serve_forever()
