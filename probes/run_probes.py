"""Opt-in native probes: check how the installed Claude Code and Codex CLIs really behave.

Each probe builds a disposable framework fixture in a temp folder, runs a real CLI with a
hard timeout (process-group kill) and a small budget, and reports exactly one status:

    PASS        the CLI behaved as the kit assumes, shown by evidence the harness recorded
    FAIL        the CLI behaved differently; the kit's assumption is wrong for this version
    UNSUPPORTED the feature does not exist in this CLI version
    UNTESTED    the probe could not drive the behaviour (headless limits, budget, no CLI)

Delivery is proven by what hook stubs write to their own log and by the CLI's event stream,
never by the model's self-report alone. Without `--live` nothing runs: the probe list is
printed and the script exits 0, so ordinary CI never spends money or calls a CLI.

Usage (from the kit root):
    uv run --isolated --no-project --python ">=3.11" -- python probes/run_probes.py --live
        [--only PREFIX ...] [--model haiku] [--codex-model MODEL] [--budget 1.00]
        [--no-codex] [--framework PATH] [--workdir DIR] [--keep]

Exit codes: 0 no FAIL, 1 at least one FAIL, 2 could not run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

KIT = Path(__file__).resolve().parents[1]
STATUSES = ("PASS", "FAIL", "UNSUPPORTED", "UNTESTED")
CLAUDE_TIMEOUT = 240
CODEX_TIMEOUT = 330

STUB = r'''#!/usr/bin/env python3
"""Probe hook stub: log the stdin payload beside this file, then act on argv[1]."""
import json, os, sys, time
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "log"
tag = sys.argv[2] if len(sys.argv) > 2 else ""
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except ValueError:
    payload = {"_raw": raw}
record = {
    "time": time.time(), "mode": mode, "tag": tag, "argv": sys.argv, "cwd": os.getcwd(),
    "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR"), "payload": payload,
}
with open(Path(__file__).resolve().parent / "hook-log.jsonl", "a") as log:
    log.write(json.dumps(record) + "\n")
if mode == "deny":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"{tag} denied by the probe hook"}}))
elif mode == "crash":
    raise RuntimeError("probe hook crashed on purpose")
elif mode == "context":
    print(f"{tag}-{payload.get('source', 'none')}")
'''


# A stdio MCP server whose one tool answers permission prompts with a deny. It gives a
# headless run the "permission host" that -p needs before it offers ExitPlanMode.
HOST = r"""import json, sys
from pathlib import Path
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") is None:
        continue
    method, params = msg.get("method"), msg.get("params", {})
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "0"}}
    elif method == "tools/list":
        schema = {"type": "object", "properties": {"tool_name": {"type": "string"},
                                                   "input": {"type": "object"}}}
        result = {"tools": [{"name": "host", "description": "answer permission prompts",
                             "inputSchema": schema}]}
    elif method == "tools/call":
        with open(Path(__file__).resolve().parent / "host-log.jsonl", "a") as log:
            log.write(json.dumps(params.get("arguments", {})) + "\n")
        answer = {"behavior": "deny", "message": "probe host denies"}
        result = {"content": [{"type": "text", "text": json.dumps(answer)}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""


class BudgetExhausted(Exception):
    """The run's spending cap was reached; remaining paid probes are UNTESTED."""


@dataclass
class Result:
    id: str
    claim: str
    status: str
    evidence: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    seconds: float = 0.0


@dataclass
class Proc:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    seconds: float


@dataclass
class Run:
    """One headless Claude run: its events, final result and the saved raw stream."""

    proc: Proc
    events: list[dict[str, Any]]
    result: dict[str, Any]
    raw: Path

    @property
    def cost(self) -> float:
        return float(self.result.get("total_cost_usd") or 0.0)

    def init(self) -> dict[str, Any]:
        return next((e for e in self.events if e.get("subtype") == "init"), {})

    def tool_uses(self) -> list[dict[str, Any]]:
        return [
            block
            for e in self.events
            if e.get("type") == "assistant"
            for block in e.get("message", {}).get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]

    def model_input(self) -> str:
        """Everything fed back to the model (tool results, injected context), as text.
        Hook lifecycle events (`--include-hook-events`) are excluded: they echo hook
        stdout whether or not the model ever sees it."""
        return "\n".join(json.dumps(e) for e in self.events if e.get("type") == "user")

    def reply(self) -> str:
        return str(self.result.get("result", ""))

    def summary(self) -> str:
        r = self.result
        return (
            f"claude exit={self.proc.returncode} timed_out={self.proc.timed_out} "
            f"subtype={r.get('subtype')} turns={r.get('num_turns')} cost=${self.cost:.4f} "
            f"raw={self.raw}"
        )


def run_proc(
    argv: list[str], cwd: Path, timeout: float, stdin: str = "", env: dict[str, str] | None = None
) -> Proc:
    """Run with a hard timeout; the whole process group is killed afterwards in any case."""
    start = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 - fixed CLI argv, no shell
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    timed_out = False
    try:
        out, err = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    return Proc(process.returncode, out, err, timed_out, time.monotonic() - start)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False, timeout=60
    )
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)} in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


def token(prefix: str) -> str:
    return f"PRB{prefix}{secrets.token_hex(4).upper()}"


class Probes:
    def __init__(self, args: argparse.Namespace, work: Path) -> None:
        self.args = args
        self.work = work
        self.fixtures = work / "fixtures"
        self.raw = work / "raw"
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.raw.mkdir(parents=True, exist_ok=True)
        self.spent = 0.0

    # ----- fixtures -----

    def fixture(self, name: str, *, claude_md: bool = False) -> tuple[Path, dict[str, str]]:
        """A framework repo with a root and a workspace AGENTS.md, a nested product clone
        (`shop/repo`, ignored) and a hook stub. Returns the root and its codewords."""
        root = self.fixtures / name
        (root / "hooks").mkdir(parents=True)
        (root / "shop" / "docs").mkdir(parents=True)
        (root / "hooks" / "stub.py").write_text(STUB)
        words = {"root": token("ROOT"), "ws": token("WS"), "claude": token("CLMD")}
        (root / "AGENTS.md").write_text(
            f"# probe framework\n\n- Probe codeword: {words['root']}. Repeat it when asked for"
            " probe codewords.\n"
        )
        (root / "shop" / "AGENTS.md").write_text(
            f"# shop\n\n- Probe codeword: {words['ws']}. Repeat it when asked for probe"
            " codewords.\n"
        )
        (root / "shop" / "docs" / "notes.md").write_text("# notes\n")
        if claude_md:
            (root / "CLAUDE.md").write_text(f"# team\n\n- Probe codeword: {words['claude']}.\n")
        (root / ".gitignore").write_text("*/repo/\nhooks/hook-log.jsonl\n")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "probe@example.invalid")
        git(root, "config", "user.name", "probe")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "fixture")
        repo = root / "shop" / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "README.md").write_text("# product\n")
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=p@e.invalid", "-c", "user.name=p", "commit", "-qm", "init")
        return root, words

    @staticmethod
    def settings(folder: Path, hooks: dict[str, Any], **extra: Any) -> None:
        (folder / ".claude").mkdir(parents=True, exist_ok=True)
        (folder / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": hooks, **extra}, indent=2)
        )

    @staticmethod
    def stub(mode: str, tag: str = "", base: str = "${CLAUDE_PROJECT_DIR}", **extra: Any) -> Any:
        """An exec-form handler (`command` + `args`, no shell), as the kit writes them."""
        args = [f"{base}/hooks/stub.py", mode, tag]
        return {"type": "command", "command": "python3", "args": args, **extra}

    @staticmethod
    def log(root: Path) -> list[dict[str, Any]]:
        path = root / "hooks" / "hook-log.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # ----- CLIs -----

    def claude(
        self,
        name: str,
        cwd: Path,
        prompt: str | list[str],
        *,
        tools: str | None = None,
        allowed: str | None = None,
        mode: str | None = None,
        sources: str | None = "project,local",
        budget: float = 0.15,
        turns: int = 6,
        extra: list[str] | None = None,
    ) -> Run:
        if self.spent >= self.args.budget:
            raise BudgetExhausted(f"spent ${self.spent:.4f} of ${self.args.budget:.2f}")
        argv = ["claude", "-p", "--model", self.args.model, "--max-budget-usd", str(budget)]
        argv += ["--max-turns", str(turns), "--output-format", "stream-json", "--verbose"]
        argv += ["--include-hook-events", "--no-session-persistence"]
        if sources is not None:
            argv += ["--setting-sources", sources]
        if tools is not None:
            argv += ["--tools", tools]
        if allowed:
            argv += ["--allowedTools", allowed]
        if mode:
            argv += ["--permission-mode", mode]
        argv += extra or []
        if isinstance(prompt, list):  # several user turns in one process
            argv += ["--input-format", "stream-json"]
            stdin = "".join(
                json.dumps({"type": "user", "message": {"role": "user", "content": p}}) + "\n"
                for p in prompt
            )
        else:
            # Right after -p: variadic options such as --tools would swallow a trailing prompt.
            argv.insert(2, prompt)
            stdin = ""
        proc = run_proc(argv, cwd, CLAUDE_TIMEOUT, stdin)
        raw = self.raw / f"{name}.jsonl"
        raw.write_text(proc.stdout + ("\n# stderr\n" + proc.stderr if proc.stderr else ""))
        events: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            with suppress(ValueError):
                events.append(json.loads(line))
        result: dict[str, Any] = next(
            (e for e in reversed(events) if e.get("type") == "result"), {}
        )
        run = Run(proc, events, result, raw)
        self.spent += run.cost
        return run

    # ----- Claude probes -----

    def p_pretooluse_deny(self) -> list[Result]:
        """PreToolUse on Bash, exec form with ${CLAUDE_PROJECT_DIR} in args, an `if` filter
        and a deny decision: only the commit reaches the hook, and the commit is refused."""
        root, _ = self.fixture("pretooluse")
        reason = token("DENY")
        hook = self.stub("deny", reason, **{"if": "Bash(*commit*)"})
        literal = "lit $HOME *;x"  # a shell would expand or split this
        hook["args"].append(literal)
        self.settings(root, {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]})
        before = git(root, "rev-list", "--count", "HEAD")
        run = self.claude(
            "pretooluse_deny",
            root,
            "Use the Bash tool to run these two commands, one call each, exactly as written:\n"
            "1. touch control.txt\n2. git commit --allow-empty -m probe\n"
            "Do not retry, rephrase or work around a refused command. Then reply DONE.",
            tools="Bash",
            allowed="Bash",
            mode="dontAsk",
        )
        log = self.log(root)
        commands = [str(r["payload"].get("tool_input", {}).get("command", "")) for r in log]
        after = git(root, "rev-list", "--count", "HEAD")
        tried = [u for u in run.tool_uses() if "commit" in str(u.get("input", {}))]
        ev = [
            run.summary(),
            f"hook log commands: {commands}",
            f"hook CLAUDE_PROJECT_DIR={[r['CLAUDE_PROJECT_DIR'] for r in log]}",
            f"control.txt exists: {(root / 'control.txt').exists()}",
            f"commits before/after: {before}/{after}",
            f"deny reason token in model input: {reason in run.model_input()}",
        ]
        results = []
        if not tried:
            status = "UNTESTED"
            ev.append("the model never attempted the commit")
        else:
            argv0 = [r["argv"] for r in log]
            ev.insert(1, f"stub argv: {argv0}")
            exec_ok = bool(log) and all(
                a[0] == f"{root}/hooks/stub.py" and a[3:] == [literal] for a in argv0
            )
            status = "PASS" if exec_ok else "FAIL"
        results.append(
            Result(
                "claude.hook.exec_args_project_dir",
                "exec-form `args` run without a shell and expand ${CLAUDE_PROJECT_DIR}",
                status,
                ev[:4],
            )
        )
        if_ok = (root / "control.txt").exists() and not any("touch" in c for c in commands)
        results.append(
            Result(
                "claude.hook.if_filter",
                "`if: Bash(*commit*)` runs the hook for the commit only, not for other Bash calls",
                ("PASS" if if_ok and log else "FAIL") if tried else "UNTESTED",
                ev[2:5],
            )
        )
        deny_ok = before == after and reason in run.model_input()
        results.append(
            Result(
                "claude.pretooluse.deny_honoured",
                "a PreToolUse deny blocks the call and its reason reaches the model",
                ("PASS" if deny_ok else "FAIL") if tried else "UNTESTED",
                ev[4:] + [ev[0]],
                cost_usd=run.cost,
                seconds=run.proc.seconds,
            )
        )
        return results

    def p_onfailure_block(self) -> list[Result]:
        """A crashing hook blocks with `onFailure: block`; without it the call goes through."""
        outcomes: dict[bool, tuple[bool, int, Run]] = {}
        for block in (True, False):
            name = f"onfailure_{'block' if block else 'control'}"
            root, _ = self.fixture(name)
            extra = {"onFailure": "block"} if block else {}
            self.settings(
                root, {"PreToolUse": [{"matcher": "Bash", "hooks": [self.stub("crash", **extra)]}]}
            )
            run = self.claude(
                name,
                root,
                "Use the Bash tool once to run exactly: touch marker.txt\n"
                "Do not retry or work around a refusal. Then reply DONE.",
                tools="Bash",
                allowed="Bash",
                mode="dontAsk",
            )
            outcomes[block] = ((root / "marker.txt").exists(), len(self.log(root)), run)
        (made_b, calls_b, run_b), (made_c, calls_c, run_c) = outcomes[True], outcomes[False]
        ev = [
            f"onFailure=block: hook ran {calls_b}x, marker created={made_b}; {run_b.summary()}",
            f"control (no onFailure): hook ran {calls_c}x, marker created={made_c}; "
            f"{run_c.summary()}",
        ]
        status = "UNTESTED" if not calls_b else ("FAIL" if made_b else "PASS")
        if made_c is False and calls_c:
            ev.append("control was also blocked: a crash alone blocks, onFailure is not shown")
        return [
            Result(
                "claude.hook.onfailure_block",
                "`onFailure: block` turns a crashing PreToolUse hook into a block",
                status,
                ev,
                cost_usd=run_b.cost + run_c.cost,
                seconds=run_b.proc.seconds + run_c.proc.seconds,
            )
        ]

    def p_exitplanmode(self) -> list[Result]:
        """The ExitPlanMode PreToolUse payload carries the plan text in tool_input.plan."""
        root, _ = self.fixture("exitplan")
        # plansDirectory keeps the plan file in the fixture instead of ~/.claude/plans.
        self.settings(
            root,
            {"PreToolUse": [{"matcher": "ExitPlanMode", "hooks": [self.stub("deny", "PLAN")]}]},
            plansDirectory="plans",
        )
        # -p offers ExitPlanMode only with a permission host: a stub MCP tool that denies.
        # The denying hook answers first, so the host should never be asked.
        (root / "hooks" / "host.py").write_text(HOST)
        mcp = {
            "mcpServers": {"probe": {"command": "python3", "args": [str(root / "hooks/host.py")]}}
        }
        (root / "mcp.json").write_text(json.dumps(mcp))
        run = self.claude(
            "exitplanmode",
            root,
            "Plan how to create hello.txt containing the word hi, in two short steps. Do not"
            " explore the repository. Present the plan by calling ExitPlanMode.",
            mode="plan",
            turns=4,
            extra=[
                "--mcp-config",
                str(root / "mcp.json"),
                "--strict-mcp-config",
                "--permission-prompt-tool",
                "mcp__probe__host",
            ],  # fmt: skip
        )
        host_calls = (root / "hooks" / "host-log.jsonl").is_file()
        offered = "ExitPlanMode" in (run.init().get("tools") or [])
        log = self.log(root)
        plans = [r["payload"].get("tool_input", {}) for r in log]
        plan_files = sorted(str(p) for p in (root / "plans").glob("*.md"))
        ev = [run.summary(), f"ExitPlanMode in init tools: {offered}; plan files: {plan_files}"]
        shown = [(sorted(p), str(p.get("plan"))[:120]) for p in plans]
        ev.append(f"hook payload tool_input keys/plan: {shown}")
        ev.append(f"permission host asked after the hook denied: {host_calls}")
        if not log:
            status = "UNTESTED"
            ev.append("hook never fired: ExitPlanMode is not drivable in this headless run")
        else:
            status = "PASS" if any(str(p.get("plan", "")).strip() for p in plans) else "FAIL"
        return [
            Result(
                "claude.exitplanmode.plan_in_payload",
                "PreToolUse on ExitPlanMode receives the plan text in tool_input.plan",
                status,
                ev,
                cost_usd=run.cost,
                seconds=run.proc.seconds,
            )
        ]

    def p_loading_and_resolution(self) -> list[Result]:
        """Launch folders: root, workspace, a workspace subfolder, inside the nested clone,
        and a workspace inside a git worktree. Hook resolution comes from the stub log and
        CLAUDE_PROJECT_DIR; AGENTS.md loading from init.memory_paths and the reply."""
        root, words = self.fixture("loading")
        ss_root, ss_ws = token("SSROOT"), token("SSWS")
        self.settings(root, {"SessionStart": [{"hooks": [self.stub("context", ss_root)]}]})
        self.settings(
            root / "shop",
            {
                "SessionStart": [
                    {"hooks": [self.stub("context", ss_ws, "${CLAUDE_PROJECT_DIR}/..")]}
                ]
            },
        )
        git(root, "add", "-A")
        git(root, "commit", "-qm", "settings")
        tree = self.fixtures / "loading-wt"
        git(root, "worktree", "add", "-q", str(tree), "-b", "lane")
        question = (
            "Without using any tools, list every probe codeword (tokens starting with PRB) that"
            " appears in your instructions or context, space-separated. If none, reply NONE."
        )
        cases = [
            ("root", root, root),
            ("workspace", root / "shop", root),
            ("workspace_subfolder", root / "shop" / "docs", root),
            ("nested_clone", root / "shop" / "repo", root),
            ("worktree_workspace", tree / "shop", tree),
        ]
        results: list[Result] = []
        for name, cwd, log_root in cases:
            before = len(self.log(log_root))
            # The user's normal setting sources: loading is checked as it really runs.
            run = self.claude(f"loading_{name}", cwd, question, tools="", sources=None, turns=1)
            fired = self.log(log_root)[before:]
            memory = [str(p) for p in run.init().get("memory_paths") or []]
            reply = run.reply()
            got = {k: w in reply for k, w in words.items() if k != "claude"}
            hooks = [
                (r["tag"][:6], r["CLAUDE_PROJECT_DIR"], r["payload"].get("source")) for r in fired
            ]
            ev = [
                run.summary(),
                f"cwd={cwd}",
                f"memory_paths={memory}",
                f"codewords in reply={got}; hook output in reply:"
                f" root={ss_root in reply} ws={ss_ws in reply}",
                f"hooks fired (tag, CLAUDE_PROJECT_DIR, source)={hooks}",
            ]
            tags = [r["tag"] for r in fired]
            dirs = {r["CLAUDE_PROJECT_DIR"] for r in fired}
            if name == "root":
                ok = tags == [ss_root] and dirs == {str(root)} and got["root"]
                claim = (
                    "root launch: root hooks run with CLAUDE_PROJECT_DIR=root; root AGENTS.md loads"
                )
            elif name == "workspace":
                ok = tags == [ss_ws] and dirs == {str(cwd)} and got["root"] and got["ws"]
                claim = (
                    "workspace launch: only the workspace's hooks run (`${CLAUDE_PROJECT_DIR}/..`"
                    " resolves); root and workspace AGENTS.md both load"
                )
            elif name == "workspace_subfolder":
                ok = not fired
                claim = (
                    "kit: settings load from the launch folder only, so a launch in a workspace"
                    " subfolder runs no workspace hooks"
                )
            elif name == "nested_clone":
                ok = not fired
                claim = "kit: a launch inside `repo/` loads none of the workspace's hooks"
            else:
                ok = tags == [ss_ws] and dirs == {str(cwd)} and got["ws"]
                claim = (
                    "worktree launch: hooks resolve inside the worktree (its own stub, its own"
                    " CLAUDE_PROJECT_DIR), not the main checkout"
                )
            status = "PASS" if ok else "FAIL"
            if run.proc.timed_out or not run.result:
                status = "UNTESTED"
            results.append(
                Result(f"claude.launch.{name}", claim, status, ev, run.cost, run.proc.seconds)
            )
        # A CLAUDE.md above the launch folder turns AGENTS.md loading off.
        alt, alt_words = self.fixture("loading-claude-md", claude_md=True)
        run = self.claude(
            "loading_claude_md", alt / "shop", question, tools="", sources=None, turns=1
        )
        reply = run.reply()
        has = {k: w in reply for k, w in alt_words.items()}
        memory = [str(p) for p in run.init().get("memory_paths") or []]
        ok = has["claude"] and not has["root"] and not has["ws"]
        results.append(
            Result(
                "claude.agents_md.ancestor_claude_md_disables",
                "a CLAUDE.md above the launch folder stops native AGENTS.md loading (why the kit"
                " writes no CLAUDE.md)",
                "PASS" if ok else ("UNTESTED" if not run.result else "FAIL"),
                [run.summary(), f"memory_paths={memory}", f"codewords in reply={has}"],
                run.cost,
                run.proc.seconds,
            )
        )
        return results

    def p_compaction(self) -> list[Result]:
        """SessionStart `compact` fires after /compact and its output reaches the model."""
        root, _ = self.fixture("compact")
        tag = token("SS")
        self.settings(
            root,
            {
                "SessionStart": [
                    {"matcher": "startup|compact", "hooks": [self.stub("context", tag)]}
                ]
            },
        )
        run = self.claude(
            "compaction",
            root,
            [
                "Reply with the single word READY.",
                "/compact",
                "Without tools, list every token starting with PRB in your context, "
                "space-separated, or NONE.",
            ],
            tools="",
            turns=3,
            budget=0.25,
        )
        sources = [r["payload"].get("source") for r in self.log(root)]
        fed = run.model_input()
        after = f"{tag}-compact"
        ev = [
            run.summary(),
            f"SessionStart sources logged: {sources}",
            f"`{after}` in model input: {after in fed}; in final reply: {after in run.reply()}",
        ]
        if "compact" not in sources:
            status = "UNTESTED"
            ev.append("compaction did not happen in this headless run")
        else:
            status = "PASS" if after in fed or after in run.reply() else "FAIL"
        return [
            Result(
                "claude.sessionstart.compact_reinjects",
                "SessionStart with matcher `compact` runs after compaction and its stdout is"
                " re-injected",
                status,
                ev,
                run.cost,
                run.proc.seconds,
            )
        ]

    # ----- Codex probes -----

    def cross_review(self) -> Any:
        scripts = (self.args.framework or KIT / "files") / "ai" / "scripts" / "cross_review.py"
        spec = importlib.util.spec_from_file_location("probe_cross_review", scripts)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {scripts}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def p_codex_schema(self) -> list[Result]:
        """The kit's own `codex exec` call (cross_review.codex) returns output that matches the
        kit's schema; a staged, obviously wrong change is reviewed end to end."""
        cr = self.cross_review()
        repo = self.fixtures / "codex-review"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "probe@example.invalid")
        git(repo, "config", "user.name", "probe")
        (repo / "calc.py").write_text(
            'def add(a, b):\n    """Return the sum."""\n    return a + b\n'
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "init")
        (repo / "calc.py").write_text(
            'def add(a, b):\n    """Return the sum."""\n    return a - b\n'
        )
        git(repo, "add", "-A")
        start = time.monotonic()
        receipt = cr.review_staged(repo, [], model=self.args.codex_model)
        seconds = time.monotonic() - start
        run = Path(receipt.get("run", ""))
        usage = ""
        events = run / "events.jsonl"
        if events.is_file():
            usage = next(
                (line for line in events.read_text().splitlines() if '"turn.completed"' in line), ""
            )[:300]
        result = receipt.get("result") or {}
        ev = [
            f"receipt approved={receipt.get('approved')} error={receipt.get('error')}",
            f"findings={[(f['priority'], f['issue'][:80]) for f in result.get('findings', [])]}",
            f"complete={result.get('complete')} usage={usage}",
            f"run folder: {run}",
        ]
        ok = "error" not in receipt and bool(result)
        if ok:
            cr.validate_result(result)  # raises on a schema mismatch
        caught = any(f["priority"] in {"P0", "P1", "P2"} for f in result.get("findings", []))
        ev.append(f"blocking finding on the seeded bug: {caught}")
        return [
            Result(
                "codex.exec.output_schema",
                "`codex exec --output-schema` with the kit's schema and flags yields a valid"
                " structured review",
                "PASS" if ok else "FAIL",
                ev,
                seconds=seconds,
            )
        ]

    def p_codex_loading(self) -> list[Result]:
        """`codex debug prompt-input` (no model call) shows which AGENTS.md files load."""
        root, words = self.fixture("codex-loading")
        results = []
        for name, cwd, want in (
            ("workspace", root / "shop", {"root": True, "ws": True}),
            ("nested_clone", root / "shop" / "repo", {"root": False, "ws": False}),
        ):
            proc = run_proc(["codex", "debug", "prompt-input", "probe"], cwd, 120)
            got = {k: words[k] in proc.stdout for k in ("root", "ws")}
            status = "PASS" if got == want else "FAIL"
            if proc.returncode or proc.timed_out:
                status = "UNTESTED"
            claim = (
                "Codex loads the root and workspace AGENTS.md from a workspace launch"
                if name == "workspace"
                else "kit: Codex launched inside `repo/` treats the clone as the root and loads"
                " neither framework AGENTS.md"
            )
            results.append(
                Result(
                    f"codex.agents_md.{name}",
                    claim,
                    status,
                    [f"exit={proc.returncode} codewords present={got}", proc.stderr.strip()[:200]],
                    seconds=proc.seconds,
                )
            )
        return results

    def p_codex_hooks(self) -> list[Result]:
        """The kit's Codex SessionStart hook form fires from a workspace launch. Trust is
        given per invocation (`-c projects={"<root>"={trust_level=...}}`, the table form: the dotted
        form is silently ignored, plus `--dangerously-bypass-hook-trust`)
        so nothing is written to ~/.codex."""
        root, _ = self.fixture("codex-hooks")
        tag = token("CXSS")
        command = f'python3 "$(git rev-parse --show-toplevel)/hooks/stub.py" context {tag}'
        (root / ".codex").mkdir()
        (root / ".codex" / "hooks.json").write_text(
            json.dumps(
                {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}
            )
        )
        argv = [
            "codex", "exec", "--cd", str(root / "shop"), "--sandbox", "read-only", "--ephemeral",
            "--json", "-c", f'projects={{"{root}"={{trust_level="trusted"}}}}',
            "--dangerously-bypass-hook-trust",
        ]  # fmt: skip
        if self.args.codex_model:
            argv += ["--model", self.args.codex_model]
        argv.append("Reply with the single word OK and nothing else. Do not run commands.")
        proc = run_proc(argv, root / "shop", CODEX_TIMEOUT)
        log = self.log(root)
        ev = [
            f"codex exit={proc.returncode} timed_out={proc.timed_out}",
            "hook log: "
            + str([(r["payload"].get("hook_event_name"), r["payload"].get("source")) for r in log]),
            f"hook output in event stream: {tag in proc.stdout}",
            proc.stderr.strip()[-300:],
        ]
        status = "PASS" if log else ("UNTESTED" if proc.returncode or proc.timed_out else "FAIL")
        return [
            Result(
                "codex.hooks.session_start_workspace",
                "Codex runs the root `.codex/hooks.json` SessionStart hook from a workspace launch"
                " (`$(git rev-parse --show-toplevel)` finds the framework)",
                status,
                ev,
                seconds=proc.seconds,
            )
        ]


PROBES: list[tuple[str, str, bool]] = [
    # (method, id prefix, paid)
    ("p_pretooluse_deny", "claude.hook", True),
    ("p_onfailure_block", "claude.hook.onfailure", True),
    ("p_exitplanmode", "claude.exitplanmode", True),
    ("p_loading_and_resolution", "claude.launch", True),
    ("p_compaction", "claude.sessionstart", True),
    ("p_codex_loading", "codex.agents_md", False),
    ("p_codex_schema", "codex.exec", True),
    ("p_codex_hooks", "codex.hooks", True),
]


def table(results: list[Result]) -> str:
    lines = ["| Probe | Status | Claim | Evidence |", "|---|---|---|---|"]
    for r in results:
        evidence = "<br>".join(e.replace("|", "\\|").replace("\n", " ") for e in r.evidence if e)
        lines.append(f"| `{r.id}` | {r.status} | {r.claim} | {evidence} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="actually run the CLIs (costs money)")
    parser.add_argument("--only", nargs="*", default=[], help="method or id prefixes to run")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--codex-model", default=None, help="default: Codex's configured model")
    parser.add_argument("--budget", type=float, default=1.0, help="Claude USD cap for the run")
    parser.add_argument("--no-codex", action="store_true")
    parser.add_argument("--framework", type=Path, help="use this framework's ai/scripts")
    parser.add_argument("--workdir", type=Path, help="default: a new temp folder")
    parser.add_argument("--keep", action="store_true", help="keep the fixtures")
    args = parser.parse_args(argv)
    selected = [
        p
        for p in PROBES
        if (not args.only or any(p[0].startswith(o) or p[1].startswith(o) for o in args.only))
        and not (args.no_codex and p[1].startswith("codex"))
    ]
    if not args.live:
        print("Probes (not run; pass --live to run them, which calls the CLIs and costs money):")
        for method, prefix, paid in selected:
            doc = (getattr(Probes, method).__doc__ or "").split("\n")[0]
            print(f"  {prefix:28s} {'paid' if paid else 'free'}  {doc}")
        return 0
    missing = [cli for cli in ("claude", "codex", "git", "python3") if not shutil.which(cli)]
    if "claude" in missing or "git" in missing or "python3" in missing:
        print(f"could not run: missing {missing}", file=sys.stderr)
        return 2
    work = args.workdir or Path(tempfile.mkdtemp(prefix="kit-probes-"))
    work.mkdir(parents=True, exist_ok=True)
    probes = Probes(args, work)
    versions = {
        cli: run_proc([cli, "--version"], work, 30).stdout.strip()
        for cli in ("claude", "codex")
        if cli not in missing
    }
    results: list[Result] = []
    for method, prefix, _ in selected:
        if prefix.startswith("codex") and "codex" in missing:
            results.append(Result(prefix, "codex CLI", "UNTESTED", ["codex not on PATH"]))
            continue
        start = time.monotonic()
        try:
            results.extend(getattr(probes, method)())
        except BudgetExhausted as exc:
            results.append(Result(prefix, method, "UNTESTED", [f"budget exhausted: {exc}"]))
        except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the others
            results.append(Result(prefix, method, "UNTESTED", [f"probe error: {exc!r}"]))
        print(f"{method}: done in {time.monotonic() - start:.0f}s, spent ${probes.spent:.4f}")
    report = {
        "date": time.strftime("%Y-%m-%d"),
        "versions": versions,
        "model": args.model,
        "claude_cost_usd": round(probes.spent, 4),
        "results": [asdict(r) for r in results],
    }
    (work / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    header = (
        f"Probes {report['date']}: {versions}; claude model {args.model}; "
        f"Claude spend ${probes.spent:.4f}\n\n"
    )
    (work / "results.md").write_text(header + table(results) + "\n")
    print(header + table(results))
    print(f"\nresults: {work / 'results.json'} and {work / 'results.md'}")
    if not args.keep:
        shutil.rmtree(probes.fixtures, ignore_errors=True)
    counts = {s: sum(r.status == s for r in results) for s in STATUSES}
    print("summary: " + ", ".join(f"{s} {n}" for s, n in counts.items()))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
