"""Start the service on a free port, wait for readiness, exercise it, always stop it."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _request(url: str, data: object = None) -> tuple[int, object]:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


class HiddenServiceTest(unittest.TestCase):
    port: int
    proc: subprocess.Popen[bytes]

    @classmethod
    def setUpClass(cls) -> None:
        cls.port = _free_port()
        env = dict(os.environ, PORT=str(cls.port))
        cls.log = tempfile.TemporaryFile()  # The service's output, for a failure message.
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "app.server"], env=env, start_new_session=True,
            stdout=cls.log, stderr=subprocess.STDOUT,
        )
        record = os.environ.get("EVAL_RUNTIME_RECORD")
        if record:
            Path(record).write_text(json.dumps({"pid": cls.proc.pid, "port": cls.port}))
        deadline = time.monotonic() + 30  # Cold interpreter starts on CI runners are slow.
        last: object = None
        while True:
            try:
                status = _request(f"http://127.0.0.1:{cls.port}/health")[0]
                if status == 200:
                    return
                last = f"HTTP {status}"
            except (OSError, ValueError) as err:
                last = repr(err)
            if cls.proc.poll() is not None or time.monotonic() > deadline:
                code = cls.proc.poll()
                cls.log.seek(0)
                output = cls.log.read().decode(errors="replace")[-2000:]
                cls.tearDownClass()
                try:
                    socket.create_connection(("127.0.0.1", cls.port), timeout=2).close()
                    tcp = "connects"
                except OSError as err:
                    tcp = repr(err)
                raise RuntimeError(
                    f"service did not become ready (exit {code}, last {last}, tcp {tcp}): {output}"
                )
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.proc.poll() is None:
            os.killpg(cls.proc.pid, signal.SIGTERM)
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(cls.proc.pid, signal.SIGKILL)
                cls.proc.wait()
        cls.log.close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_create_then_fetch(self) -> None:
        status, created = _request(self.url("/notes"), {"text": "hello"})
        self.assertEqual(status, 201)
        assert isinstance(created, dict)
        status, fetched = _request(self.url(f"/notes/{created['id']}"))
        self.assertEqual((status, fetched), (200, {"id": created["id"], "text": "hello"}))

    def test_ids_are_distinct(self) -> None:
        _, a = _request(self.url("/notes"), {"text": "a"})
        _, b = _request(self.url("/notes"), {"text": "b"})
        assert isinstance(a, dict) and isinstance(b, dict)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(_request(self.url(f"/notes/{b['id']}"))[1], {"id": b["id"], "text": "b"})

    def test_unknown_is_404(self) -> None:
        self.assertEqual(_request(self.url("/notes/999999"))[0], 404)
