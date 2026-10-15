"""Windows only (kit CI): the product gate runner imports and bounds gates on Windows.

The kit's scripts target macOS and Linux; check_code.py also runs on Windows, where a
timeout or interruption stops the gate's process tree with `taskkill /T /F`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("Windows behaviour; covered by the kit's Windows CI job", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "files" / "ai" / "scripts"))

import check_code  # noqa: E402

HANG = (
    "import subprocess, sys, time; "
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
    "open(sys.argv[1], 'w').write(str(child.pid)); time.sleep(600)"
)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def workspace(tmp_path: Path, gates: str) -> Path:
    ws = tmp_path / "shop"
    (ws / "repo").mkdir(parents=True)
    git(ws / "repo", "init", "-q")
    git(ws / "repo", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q",
        "--allow-empty", "-m", "init")  # fmt: skip
    (ws / "workspace.toml").write_text(f'name = "shop"\nlocal_only = true\n{gates}')
    return ws


def gate(name: str, code: str, extra: str = "") -> str:
    argv = ", ".join(json.dumps(w) for w in [sys.executable, "-c", code])
    return f'[[lint]]\nname = "{name}"\nargv = [{argv}{extra}]\n'


def alive(pid: int) -> bool:
    found = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    ).stdout
    return str(pid) in found


def test_scripts_import_on_windows() -> None:
    import format_changed  # noqa: F401
    import garden  # noqa: F401
    import verify_agent_env  # noqa: F401
    import workspace_profile  # noqa: F401


def test_pass_fail_and_timeout(tmp_path: Path) -> None:
    ws = workspace(tmp_path, gate("ok", "pass") + gate("bad", "raise SystemExit(1)"))
    assert check_code.run(["lint", "--skip", "bad"], cwd=ws) == 0
    assert check_code.run(["lint"], cwd=ws) == 1
    pid_file = tmp_path / "pid"
    hang = gate("hang", HANG, ", " + json.dumps(str(pid_file))) + "timeout = 3\n"
    ws2 = workspace(tmp_path / "two", hang)
    start = time.monotonic()
    assert check_code.run(["lint"], cwd=ws2) == 1  # A timeout never passes.
    assert time.monotonic() - start < 60
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not alive(child)  # The whole process tree was stopped.
