"""Notes HTTP service. Run with ``PORT=<port> python -m app.server``."""

import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_NOTES: dict[int, str] = {}
_LOCK = threading.Lock()


def parse_note_id(path: str) -> int | None:
    """Return the id in ``/notes/<id>``, or None."""
    prefix = "/notes/"
    if path.startswith(prefix) and path[len(prefix) :].isdigit():
        return int(path[len(prefix) :])
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        note_id = parse_note_id(self.path)
        with _LOCK:
            text = _NOTES.get(note_id) if note_id is not None else None
        if text is None:
            self._send(404, {"error": "not found"})
        else:
            self._send(200, {"id": note_id, "text": text})

    def do_POST(self) -> None:
        if self.path != "/notes":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        with _LOCK:
            note_id = len(_NOTES) + 1
            _NOTES[note_id] = str(payload.get("text", ""))
        self._send(201, {"id": note_id})

    def log_message(self, format: str, *args: object) -> None:
        pass


class Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer.server_bind does a reverse-DNS lookup (socket.getfqdn), which can
        # hang for a long time on some hosts; a local test service needs no name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def main() -> None:
    server = Server(("127.0.0.1", int(os.environ["PORT"])), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
