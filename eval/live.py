"""Opt-in live trials: one bounded ``claude -p`` run per task x treatment x trial.

Each trial gets a fresh temporary folder built by the treatment, so no trial can see
another's patch or transcript. Bounds: a hard wall-clock timeout that kills the whole
process group, ``--max-turns``, ``--max-budget-usd`` per trial, and a total budget the
runner stops at. Sessions are not persisted (``--no-session-persistence``), user and
local settings are not loaded (``--setting-sources project``), no MCP servers load
(``--strict-mcp-config``) and auto-memory is disabled by environment variable.

Trials run with a minimal environment (``TRIAL_ENV``), so credentials in the caller's
environment (cloud keys, tokens) do not reach the agent. They are not sandboxed: the
agent's Bash runs as the user, with network access, and can read any path the user can,
including this kit's ``eval/fixtures`` (hidden tests, solutions), so ``run.py --live``
requires ``--allow-unsandboxed-bash``. Run it on a machine and account where that is
acceptable. Also not prevented: user-level instructions outside settings (for example
``~/.claude/CLAUDE.md``) may load; the model API may cache prompts across trials (a cost
effect, not shared memory).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness import run_bounded

ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
# What a trial inherits: enough to run tools and authenticate (macOS keychain via HOME).
TRIAL_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
QUESTION_RE = re.compile(
    r"\b(should i|shall i|would you like|do you want|could you (?:confirm|clarify)|"
    r"which (?:option|approach) (?:do|would) you)\b",
    re.IGNORECASE,
)


@dataclass
class LiveConfig:
    model: str
    max_turns: int
    max_budget_usd: float
    timeout_s: float


def build_claude_cmd(prompt: str, cfg: LiveConfig) -> list[str]:
    return [
        "claude",
        "-p",
        prompt,
        "--model",
        cfg.model,
        "--max-turns",
        str(cfg.max_turns),
        "--max-budget-usd",
        f"{cfg.max_budget_usd:.2f}",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ALLOWED_TOOLS,
    ]


def claude_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in TRIAL_ENV or k.startswith("ANTHROPIC_")}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


def claude_version() -> str:
    try:
        done = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"unavailable: {err}"
    return done.stdout.strip() or f"unavailable: exit {done.returncode}"


@dataclass
class Trace:
    result: dict[str, Any] | None = None
    context_files_read: list[str] = field(default_factory=list)
    tool_calls: int = 0
    ask_user_calls: int = 0
    parse_errors: int = 0


def parse_stream(text: str, trial_root: Path) -> Trace:
    """Extract the result message and context-relevant tool use from stream-json."""
    trace = Trace()
    root = str(trial_root.resolve())
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            trace.parse_errors += 1
            continue
        if msg.get("type") == "result":
            trace.result = msg
        if msg.get("type") != "assistant":
            continue
        for block in msg.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            trace.tool_calls += 1
            name, args = block.get("name"), block.get("input", {})
            if name == "AskUserQuestion":
                trace.ask_user_calls += 1
            target = ""
            if name == "Read":
                target = str(args.get("file_path", ""))
            elif name == "Bash":
                target = str(args.get("command", ""))
            for hit in re.findall(r"(AGENTS\.md|docs/[\w./-]+\.md|ai/tasks/[\w./-]+\.md)", target):
                rel = hit.removeprefix(root + "/")
                if rel not in seen:
                    seen.add(rel)
                    trace.context_files_read.append(rel)
    return trace


def question_count(result_text: str) -> int:
    """Heuristic: sentences in the final message that ask the user to decide."""
    return sum(
        1
        for s in re.split(r"(?<=[?.!])\s+", result_text)
        if s.rstrip().endswith("?") and QUESTION_RE.search(s)
    )


def run_agent(prompt: str, trial_root: Path, cfg: LiveConfig, evidence: Path) -> dict[str, Any]:
    """Run claude in ``trial_root``; return the agent half of a results row."""
    start = time.monotonic()
    try:
        done = run_bounded(
            build_claude_cmd(prompt, cfg),
            cwd=trial_root,
            timeout=cfg.timeout_s,
            env=claude_env(),
            stdout_path=evidence / "stream.jsonl",
        )
    except OSError as err:
        return {
            "status": "blocked",
            "error": f"could not start claude: {err}",
            "elapsed_s": round(time.monotonic() - start, 1),
        }
    elapsed = round(time.monotonic() - start, 1)
    (evidence / "stderr.txt").write_text(done.stderr, encoding="utf-8")
    trace = parse_stream(done.stdout, trial_root)
    res = trace.result or {}
    usage = res.get("usage", {})
    text = str(res.get("result", ""))
    status = "completed"
    error = None
    if done.timed_out:
        status, error = "timeout", f"killed after {cfg.timeout_s}s"
    elif trace.result is None:
        status = "blocked"
        error = f"no result message; exit {done.returncode}: {done.stderr[-500:]}"
    elif res.get("is_error"):
        status, error = "agent_error", str(res.get("subtype"))
    return {
        "status": status,
        "error": error,
        "agent_exit": done.returncode,
        "elapsed_s": elapsed,
        "agent_duration_ms": res.get("duration_ms"),
        "num_turns": res.get("num_turns"),
        "terminal_reason": res.get("terminal_reason") or res.get("subtype"),
        "model": ",".join(res.get("modelUsage", {}).keys()) or None,
        "cost_usd": res.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "permission_denials": len(res.get("permission_denials", []) or []),
        "tool_calls": trace.tool_calls,
        "context_files_read": trace.context_files_read,
        "unnecessary_questions": question_count(text) + trace.ask_user_calls,
        "final_message": text[-2000:],
    }
