"""Garden: report context rot in the framework's living docs. It never edits a file.

Living docs: the root AGENTS.md, README.md, ai/project.md, ai/README.md and docs/**/*.md;
each workspace's AGENTS.md, ai/project.md and docs/**/*.md. Dated history (ai/analysis,
plans, specs, tasks, archive, at both levels) is never path-checked, and nothing inside a
clone (`<workspace>/repo`) or a lane (`<workspace>/.worktrees/`) is read as a doc.

Checks (every message states the fix):
    path      a word in an inline code span, a fenced block or a relative markdown link,
              whose first segment is a top-level entry of a tree the doc can mean, must
              exist. Framework docs mean the framework; workspace docs also mean the
              workspace and its selected checkout, including uncommitted edits, or the HEAD
              of another live lane (`<workspace>/.worktrees/`). Remote refs and branches
              without a lane do not count. Leading `../` resolves from the doc's folder.
              A line containing `garden: ignore` is skipped.
    command   every `poe <task>` in an AGENTS.md code span exists in [tool.poe.tasks] (root
              AGENTS.md: the framework's pyproject.toml; a workspace's: its clone's, unless
              the command says `--project ..`), and every `npm run`/`pnpm` script exists in
              the matching package.json.
    finished  a task list (ai/tasks/*.md) with checkboxes, none open.
    idle      a task list with open items unchanged for 14 days (last commit; file time
              while it is untracked or has uncommitted edits).
    handoff   ai/tasks/handoff_YYYYMMDD_HHMM.md older than 14 days, by its name.
    worktree  a lane whose HEAD is merged into the default branch, or idle for 14 days.
    contents  a living doc over 100 lines with no contents list in its first 15 lines.
    unrouted  a doc under a docs/ folder that no chain of citations reaches from an
              AGENTS.md or a SKILL.md. Citing a folder routes nothing.
    map       a `## Context map` table row that points at a folder instead of a doc;
              folders inside a workspace's clone are the team's, so a row may name one.
    deny      a path in a workspace's `Bash(...)` deny rule or `.codex/rules` pattern that
              the clone's default branch no longer has.
    tools     `claude --version` or `codex --version` differs from the `Last run:` line of
              docs/dev/tool-probes.md, the line is over 30 days old, or it is missing.
    context   invalid optional fenced `context` TOML (depends_on, verified_commit), or a
              depends_on path that git ignores and so cannot track.
    freshness a declared dependency changed since verified_commit, is missing, or has
              never been verified, or verified_commit is not in the checkout. This requests
              review; it does not prove a claim false.

Usage: garden.py [--root PATH] [--session | --static | --changes BASE]
                 [--workspace NAME] [--target LANE]
    (default) full report over the framework and every workspace; `--workspace NAME`
              limits the workspace checks to that one (framework checks still run).
    --session at most five summary lines, then `active: <file> (<Target line>)` per open
              task list. Launched inside a workspace, it covers that workspace only.
    --static  paths, commands, routing, contents and context metadata syntax over tracked
              framework files. Needs no clone; product freshness needs a local checkout.
    --target  select a product clone or its worktree for path, command and freshness checks.
    --changes BASE --target LANE
              removed cited paths between BASE and HEAD, plus dependency freshness
              against each document's verified_commit in the selected checkout.

Exit codes: 0 no findings, 1 findings, 2 could not run (a git call, a profile or a clone
failed; 2 is never a pass). --session always exits 0, for the SessionStart hook.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from workspace_profile import (
    ProfileError,
    find_framework_root,
    find_workspaces,
    load_profile,
    workspace_for,
)

ROOT_DOCS = ("AGENTS.md", "README.md", "ai/project.md", "ai/README.md")
WORKSPACE_DOCS = ("AGENTS.md", "ai/project.md")
KINDS = (
    *("path", "command", "finished", "idle", "handoff", "worktree"),
    *("contents", "unrouted", "map", "deny", "tools", "changes", "context", "freshness"),
)
IDLE_DAYS = 14
HANDOFF_DAYS = 14
PROBE_DAYS = 30
CONTENTS_LINES = 100
CONTENTS_HEAD = 15
DAY = 86400.0
MAINTENANCE = "docs/agent-workflows/maintenance.md"
PROBES = "docs/dev/tool-probes.md"
HINT = "run `uv run poe garden` for details"
IGNORE = "garden: ignore"

FENCE = re.compile(r"^\s*(```|~~~)")
SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*)?\)")
LINE_SUFFIX = re.compile(r"(:\d+)+(-\d+)?$")
TRAIL = ".,;:!?)]}\"'"
SKIP_CHARS = "<{*$\\"
CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX-])\]")
HANDOFF = re.compile(r"handoff_(\d{8})_(\d{4})\.md")
CONTENTS = re.compile(r"^#{1,6}\s+(table of\s+)?contents\b", re.IGNORECASE)
LINK_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[^\]]+\]\([^)]+\)")
MAP_HEAD = re.compile(r"^##\s+context map\s*$", re.IGNORECASE)
TABLE_RULE = re.compile(r"^[\s|:-]+$")
POE = re.compile(r"\bpoe\s+([\w:|-]+)")
NPM = re.compile(r"\b(npm\s+run|pnpm(?:\s+run)?)(?=\s)")
NPM_WORD = re.compile(r"\s+([\w:./@=-]+)")
PNPM_VALUE_FLAGS = {"-C", "--dir", "--filter", "-F"}
# Flags that pick another package; its package.json is not read, so the command is skipped.
PACKAGE_SELECTORS = PNPM_VALUE_FLAGS | {"--prefix", "-w", "--workspace"}
PNPM_BUILTINS = {"add", "install", "i", "exec", "dlx", "update", "up", "remove", "rm", "why"}
PNPM_BUILTINS |= {"list", "ls", "outdated", "audit", "store", "init", "create", "env", "link"}
PATTERN = re.compile(r"\bpattern\s*=\s*\[")
STRING = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
LAST_RUN = re.compile(
    r"Last run:\**\s*(\d{4}-\d{2}-\d{2}),\s*claude\s+(\d[\w.+-]*),\s*codex(?:-cli)?\s+(\d[\w.+-]*)"
)
VERSION = re.compile(r"\d+\.\d+\.\d+[\w.+-]*")
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_PREFIX")


class GardenError(Exception):
    """A check could not run (git failed, a clone is missing, a file does not parse)."""


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str
    message: str

    def line(self) -> str:
        return f"{self.kind}: {self.where}: {self.message}"


@dataclass(frozen=True)
class Workspace:
    folder: Path
    name: str
    clone: Path
    default_branch: str

    def owns(self, path: Path) -> bool:
        return path == self.folder or path.is_relative_to(self.folder)

    def unscanned(self, path: Path) -> bool:
        lanes = self.folder / ".worktrees"
        return path.is_relative_to(self.clone) or path.is_relative_to(lanes)


@dataclass(frozen=True)
class CloneIndex:
    paths: frozenset[str]  # names on any branch and in the selected checkout's index
    dirs: frozenset[str]

    @property
    def tops(self) -> set[str]:
        return {p.split("/", 1)[0] for p in self.paths}


def git(cwd: Path, *args: str, index: bool = False) -> subprocess.CompletedProcess[str]:
    """Run git in `cwd`. Inherited repo variables (set inside git hooks) are dropped, and
    GIT_INDEX_FILE is kept only for the framework repo (`index=True`)."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV}
    if not index:
        env.pop("GIT_INDEX_FILE", None)
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GardenError(f"git {' '.join(args)} in {cwd}: {exc}") from exc


def git_out(cwd: Path, *args: str, index: bool = False) -> str:
    done = git(cwd, *args, index=index)
    if done.returncode != 0:
        detail = done.stderr.strip().splitlines()[-1:] or ["failed"]
        raise GardenError(f"git {' '.join(args)} in {cwd}: {detail[0]}")
    return done.stdout


def with_dirs(files: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Return (files and their parent folders, the parent folders)."""
    files = {f for f in files if f}
    dirs = {f.rsplit("/", i)[0] for f in files for i in range(1, f.count("/") + 1)}
    return frozenset(files | dirs), frozenset(dirs)


def ref_paths(cwd: Path, ref: str) -> frozenset[str]:
    return with_dirs(git_out(cwd, "ls-tree", "-r", "--name-only", "-z", ref).split("\0"))[0]


def norm(path: Path) -> Path:
    return Path(os.path.normpath(path))


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GardenError(f"cannot read {path}: {exc}") from exc


def code_segments(text: str) -> Iterator[tuple[int, str]]:
    """Yield (line number, code text): each fenced line and each inline code span."""
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fence = not fence
        elif IGNORE in line:
            continue
        elif fence:
            yield n, line
        else:
            for m in SPAN.finditer(line):
                yield n, m.group(1)


def npm_script(code: str, m: re.Match[str]) -> tuple[str, int] | None:
    """The script an NPM match names and where it ends, past flags and pnpm flag values.

    None when there is no script, or when a flag selects another package.
    """
    pnpm, run = m.group(1).startswith("pnpm"), m.group(1) != "pnpm"
    pos, value = m.end(), False
    while word := NPM_WORD.match(code, pos):
        pos, text = word.end(), word.group(1)
        if value:
            value = False
        elif text.split("=", 1)[0] in PACKAGE_SELECTORS:
            return None
        elif text.startswith("-"):
            value = pnpm and text in PNPM_VALUE_FLAGS
        elif not run and text == "run":
            run = True
        else:
            return text, pos
    return None


def citations(text: str) -> Iterator[tuple[int, str, bool]]:
    """Yield (line number, raw word, is a markdown link target)."""
    for n, code in code_segments(text):
        for word in code.split():
            yield n, word, False
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fence = not fence
        elif not fence and IGNORE not in line:
            for m in LINK.finditer(SPAN.sub("", line)):
                yield n, m.group(1), True


def clean(word: str, link: bool) -> str | None:
    """Normalise a cited word to a relative path, or None when it cannot be one."""
    if link or ".md#" in word:
        word = word.split("#", 1)[0]
    word = word.lstrip("\"'([").rstrip(TRAIL)
    word = LINE_SUFFIX.sub("", word).rstrip(TRAIL)
    while word.startswith("./"):
        word = word[2:]
    if not word or word in {".", ".."} or "://" in word or word.startswith("mailto:"):
        return None
    if word.startswith(("~", "/", "-")) or any(c in word for c in SKIP_CHARS):
        return None
    return word


def deny_target(word: str, clone_prefix: str) -> str | None:
    """A deny-rule word as a whole clone path, or None for commands, globs and prefixes."""
    if "/" not in word:
        return None
    word = word.strip("*")
    word = word.removesuffix(":")  # the older `:*` form, after the `*` is stripped
    while word.startswith("./"):
        word = word[2:]
    word = word.removeprefix(clone_prefix)
    while word.startswith("./"):
        word = word[2:]
    if not word or word.endswith("/") or "://" in word or word.startswith(("-", "/", "~")):
        return None
    if any(c in word for c in "*?[]{}$<"):
        return None
    return word


def tool_version(name: str) -> str | None:
    """The version `<name> --version` prints, or None when the CLI is missing or fails."""
    try:
        done = subprocess.run(
            [name, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = VERSION.search(done.stdout + done.stderr) if done.returncode == 0 else None
    return m.group(0) if m else None


def age_days(now: float, then: float) -> float:
    return (now - then) / DAY


def context_metadata(text: str) -> tuple[list[str], str] | None:
    """An optional, single fenced context block; paths are literal code-root paths."""
    blocks: list[str] = []
    body: list[str] = []
    fence: str | None = None
    context = False
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None:
            match = re.fullmatch(r"(`{3,}|~{3,})(.*)", stripped)
            if match:
                fence = match.group(1)
                context = match.group(2).strip() == "context"
                body = []
        elif re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", stripped):
            if context:
                blocks.append("\n".join(body))
            fence = None
        elif context:
            body.append(line)
    if fence is not None and context:
        raise ValueError("unclosed context fence")
    if not blocks:
        return None
    if len(blocks) != 1:
        raise ValueError("use one context block per document")
    data = tomllib.loads(blocks[0])
    if set(data) != {"depends_on", "verified_commit"}:
        raise ValueError("context requires exactly depends_on and verified_commit")
    paths, revision = data["depends_on"], data["verified_commit"]
    if not isinstance(paths, list) or not paths:
        raise ValueError("depends_on must be a non-empty list of literal relative paths")
    for path in paths:
        if (
            not isinstance(path, str)
            or not path.strip()
            or path in {".", "./"}
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or ".git" in Path(path).parts
            or any(c in path for c in "*?[]\\\n\r")
        ):
            raise ValueError("depends_on paths must stay inside the code root; no globs or .git")
    if not isinstance(revision, str) or not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64}|unverified)", revision
    ):
        raise ValueError("verified_commit must be a full commit hash or 'unverified'")
    return paths, revision


class Garden:
    def __init__(
        self,
        root: Path,
        *,
        static: bool = False,
        only: str | None = None,
        target: Path | None = None,
    ) -> None:
        self.root = root
        self.static = static
        self.now = time.time()
        self.findings: list[Finding] = []
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.all_ws: list[Workspace] = []
        for folder in find_workspaces(root):
            try:
                p = load_profile(folder / "workspace.toml")
            except ProfileError as exc:
                self.errors.append(f"{exc}; fix the profile (the verifier names the bad key)")
                continue
            self.all_ws.append(Workspace(p.workspace, p.name, p.clone, p.default_branch))
        if only is not None and only not in {w.name for w in self.all_ws}:
            names = ", ".join(w.name for w in self.all_ws) or "none"
            raise GardenError(f"no loadable workspace named {only!r} (workspaces: {names})")
        self.ws = [w for w in self.all_ws if only is None or w.name == only]
        self._clones: dict[Path, CloneIndex | None] = {}
        self._lanes: dict[Path, frozenset[str]] = {}
        self._tracked: tuple[frozenset[str], frozenset[str]] | None = None
        self._tops: dict[Path, set[str]] = {}
        self._tasks: dict[Path, tuple[set[str], bool]] = {}
        self.targets: dict[Path, Path] = {}
        if target is not None:
            ws = self.select_target(target)
            if only is not None and ws.name != only:
                raise GardenError(f"target belongs to {ws.name}, not workspace {only}")
            self.ws = [ws]

    # ----- shared helpers -----

    def add(self, kind: str, where: str, message: str) -> None:
        self.findings.append(Finding(kind, where, message))

    def rel(self, path: Path) -> str:
        if path.is_relative_to(self.root):
            return path.relative_to(self.root).as_posix() or "."
        return os.path.relpath(path, self.root)

    def workspace_of(self, path: Path) -> Workspace | None:
        return next((w for w in self.all_ws if w.owns(path)), None)

    def checkout(self, ws: Workspace) -> Path:
        return self.targets.get(ws.folder, ws.clone)

    def select_target(self, target: Path) -> Workspace:
        target = target.resolve()
        ws = self.workspace_of(target)
        if ws is None:
            raise GardenError(f"{target} is not inside a workspace of {self.root}")
        if target != ws.clone and not target.is_relative_to(ws.folder / ".worktrees"):
            raise GardenError(f"{target} must be the product clone or one of its worktrees")
        top = Path(git_out(target, "rev-parse", "--show-toplevel").strip()).resolve()
        if top != target:
            raise GardenError(f"{target} is not a checkout root")
        common = ("rev-parse", "--path-format=absolute", "--git-common-dir")
        if git_out(target, *common).strip() != git_out(ws.clone, *common).strip():
            raise GardenError(f"{target} is not a worktree of {ws.clone}")
        self.targets[ws.folder] = target
        self._clones.clear()
        self._lanes.clear()
        return ws

    def physical(self, path: Path) -> Path:
        for ws in self.all_ws:
            if path.is_relative_to(ws.clone):
                return self.checkout(ws) / path.relative_to(ws.clone)
        return path

    def unscanned(self, path: Path) -> bool:
        return any(w.unscanned(path) for w in self.all_ws)

    def tracked(self) -> tuple[frozenset[str], frozenset[str]]:
        if self._tracked is None:
            out = git_out(self.root, "ls-files", "-z", index=True)
            self._tracked = with_dirs(out.split("\0"))
        return self._tracked

    def is_tracked(self, path: Path) -> bool:
        return self.rel(path) in self.tracked()[0]

    def tops(self, folder: Path) -> set[str]:
        """Top-level entries of a framework or workspace tree."""
        if folder not in self._tops:
            if self.static:
                prefix = "" if folder == self.root else self.rel(folder) + "/"
                files = self.tracked()[0]
                tops = {f[len(prefix) :].split("/", 1)[0] for f in files if f.startswith(prefix)}
            else:
                tops = set(os.listdir(folder)) if folder.is_dir() else set()
            self._tops[folder] = tops
        return self._tops[folder]

    def clone_ok(self, ws: Workspace) -> bool:
        if (self.checkout(ws) / ".git").exists():
            return True
        error = (
            f"{self.rel(ws.clone)}: clone missing; restore it with the workspace-setup skill"
            " (docs/agent-workflows/workspace-setup.md)"
        )
        if error not in self.errors:
            self.errors.append(error)
        return False

    def clone_index(self, ws: Workspace) -> CloneIndex | None:
        """Names for recognising citations, never evidence that a file still exists."""
        if ws.clone not in self._clones:
            index = None
            if self.clone_ok(ws):
                checkout = self.checkout(ws)
                files = set(git_out(checkout, "ls-files", "-z").split("\0"))
                refs = ("--format=%(objectname)", "refs/heads", "refs/remotes")
                shas = set(git_out(checkout, "for-each-ref", *refs).split())
                head = git(checkout, "rev-parse", "--verify", "-q", "HEAD")
                if head.returncode == 0:
                    shas.add(head.stdout.strip())
                for sha in sorted(shas):
                    files.update(ref_paths(checkout, sha))
                index = CloneIndex(*with_dirs(files))
            self._clones[ws.clone] = index
        return self._clones[ws.clone]

    def lane_paths(self, ws: Workspace) -> frozenset[str]:
        """Paths at the HEAD of every other live lane, since lanes share workspace docs."""
        # Not remote refs or branches without a lane: those would keep dead paths alive.
        if ws.clone not in self._lanes:
            checkout = self.checkout(ws).resolve()
            lanes = (ws.folder / ".worktrees").resolve()
            heads: set[str] = set()
            for record in git_out(checkout, "worktree", "list", "--porcelain").split("\n\n"):
                fields = {k: v for k, _, v in (line.partition(" ") for line in record.splitlines())}
                tree = Path(fields.get("worktree", "")).resolve()
                live = "worktree" in fields and "prunable" not in fields and tree.is_dir()
                lane = live and tree.is_relative_to(lanes) and tree != checkout
                if lane and fields.get("HEAD", "").strip("0"):
                    heads.add(fields["HEAD"])
            files: frozenset[str] = frozenset()
            for sha in sorted(heads):
                files |= ref_paths(checkout, sha)
            self._lanes[ws.clone] = files
        return self._lanes[ws.clone]

    def base_ref(self, ws: Workspace) -> str | None:
        for ref in (f"origin/{ws.default_branch}", ws.default_branch):
            if git(ws.clone, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0:
                return ref
        return None

    def exists(self, path: Path) -> bool:
        if self.static:
            return self.is_tracked(path)
        if self.physical(path).exists():
            return True
        ws = next((w for w in self.all_ws if path.is_relative_to(w.clone)), None)
        return (
            ws is not None
            and self.clone_ok(ws)
            and path.relative_to(ws.clone).as_posix() in self.lane_paths(ws)
        )

    def is_dir(self, path: Path) -> bool:
        return self.physical(path).is_dir()

    def trees(self, ws: Workspace | None) -> list[tuple[Path, set[str]]]:
        """The trees a doc can mean, with their top-level entries."""
        trees = [(self.root, self.tops(self.root))]
        if ws is not None:
            trees.insert(0, (ws.folder, self.tops(ws.folder)))
            index = None if self.static else self.clone_index(ws)
            if index is not None:
                trees.insert(0, (ws.clone, index.tops | self.tops(self.checkout(ws))))
        return trees

    def candidates(
        self,
        doc: Path,
        word: str,
        link: bool,
        trees: list[tuple[Path, set[str]]],
        ws: Workspace | None = None,
    ) -> list[Path]:
        """Where a cited word may point; empty when the word is not a cited path."""
        target = word.rstrip("/")
        if target.startswith("../"):
            path = norm(doc.parent / target)
            if path == self.root or not path.is_relative_to(self.root):
                return []
            first = path.relative_to(self.root).parts[0]
            found = [path] if first in self.tops(self.root) else []
        else:
            first = target.split("/", 1)[0]
            # No clone is read in static mode, so the word may name a product path.
            unseen = self.static and ws is not None and first not in self.tops(ws.folder)
            found = [] if unseen else [norm(base / target) for base, tops in trees if first in tops]
            if link:
                found.append(norm(doc.parent / target))
        if self.static:
            found = [p for p in found if not self.unscanned(p)]
        return found

    def living_docs(self) -> list[tuple[Path, Workspace | None]]:
        docs: list[tuple[Path, Workspace | None]] = [(self.root / n, None) for n in ROOT_DOCS]
        docs += [(p, None) for p in sorted((self.root / "docs").rglob("*.md"))]
        for ws in self.ws:
            docs += [(ws.folder / n, ws) for n in WORKSPACE_DOCS]
            docs += [(p, ws) for p in sorted((ws.folder / "docs").rglob("*.md"))]
        docs = [(p, w) for p, w in docs if p.is_file()]
        if self.static:
            docs = [(p, w) for p, w in docs if self.is_tracked(p)]
        return docs

    def agents_files(self) -> list[tuple[Path, Workspace | None]]:
        files: list[tuple[Path, Workspace | None]] = [(self.root / "AGENTS.md", None)]
        files += [(w.folder / "AGENTS.md", w) for w in self.ws]
        files = [(p, w) for p, w in files if p.is_file()]
        if self.static:
            files = [(p, w) for p, w in files if self.is_tracked(p)]
        return files

    def task_files(self) -> list[Path]:
        folders = [self.root / "ai" / "tasks"] + [w.folder / "ai" / "tasks" for w in self.ws]
        return sorted(p for f in folders if f.is_dir() for p in f.glob("*.md") if p.is_file())

    # ----- checks -----

    def check_paths(self) -> None:
        for doc, ws in self.living_docs():
            trees = self.trees(ws)
            for n, raw, link in citations(read(doc)):
                word = clean(raw, link)
                if word is None:
                    continue
                found = self.candidates(doc, word, link, trees, ws)
                if found and not any(self.exists(p) for p in found):
                    where = "the framework" if ws is None else "clone, workspace or framework"
                    where = "the tracked framework files" if self.static else where
                    self.add(
                        "path",
                        f"{self.rel(doc)}:{n}",
                        f"`{word}` exists nowhere in {where}; correct the path or drop the citation"
                        f" (add `{IGNORE}` to the line if it is not a path)",
                    )

    def poe_tasks(self, pyproject: Path) -> tuple[set[str], bool]:
        """(task names, file exists)."""
        if pyproject not in self._tasks:
            if not pyproject.is_file():
                self._tasks[pyproject] = (set(), False)
            else:
                try:
                    data = tomllib.loads(read(pyproject))
                except tomllib.TOMLDecodeError as exc:
                    raise GardenError(f"{pyproject}: invalid TOML: {exc}") from exc
                tasks = data.get("tool", {}).get("poe", {}).get("tasks", {})
                self._tasks[pyproject] = (set(tasks) if isinstance(tasks, dict) else set(), True)
        return self._tasks[pyproject]

    def package_scripts(self, folder: Path) -> tuple[set[str], bool]:
        package = folder / "package.json"
        if not package.is_file():
            return set(), False
        try:
            scripts = json.loads(read(package)).get("scripts", {})
        except (json.JSONDecodeError, AttributeError) as exc:
            raise GardenError(f"{package}: invalid package.json: {exc}") from exc
        return (set(scripts) if isinstance(scripts, dict) else set()), True

    def check_commands(self) -> None:
        for doc, ws in self.agents_files():
            for n, code in code_segments(read(doc)):
                for m in POE.finditer(code):
                    own = ws is None or "--project .." in code[: m.start()]
                    if self.static and not own:
                        continue
                    pyproject = (
                        self.root if own or ws is None else self.checkout(ws)
                    ) / "pyproject.toml"
                    tasks, present = self.poe_tasks(pyproject)
                    for task in m.group(1).split("|"):
                        if task and not task.startswith("-") and task not in tasks:
                            state = "" if present else " (the file is missing)"
                            self.add(
                                "command",
                                f"{self.rel(doc)}:{n}",
                                f"`poe {task}` is not a task in {self.rel(pyproject)}{state};"
                                " correct the command, or add the task to [tool.poe.tasks]",
                            )
                for m in NPM.finditer(code):
                    named = npm_script(code, m)
                    if named is None:
                        continue
                    script, end = named
                    if (ws is not None and self.static) or (
                        m.group(1) == "pnpm" and script in PNPM_BUILTINS
                    ):
                        continue
                    folder = self.root if ws is None else self.checkout(ws)
                    scripts, present = self.package_scripts(folder)
                    if script not in scripts:
                        package = self.rel(folder / "package.json")
                        state = "" if present else " (the file is missing)"
                        self.add(
                            "command",
                            f"{self.rel(doc)}:{n}",
                            f"`{code[m.start() : end]}` names no script in {package}{state};"
                            " correct the command, or add the script",
                        )

    def last_change(self, path: Path) -> float:
        """Last commit time, or file time while the file is untracked or edited."""
        rel = self.rel(path)
        status = git(self.root, "status", "--porcelain", "--", rel, index=True)
        if status.returncode == 0 and not status.stdout.strip():
            log = git(self.root, "log", "-1", "--format=%ct", "--", rel, index=True)
            if log.returncode == 0 and log.stdout.strip():
                return float(log.stdout.strip())
        return path.stat().st_mtime

    def check_tasks(self) -> None:
        for task in self.task_files():
            where = self.rel(task)
            handoff = HANDOFF.fullmatch(task.name)
            if handoff:
                try:
                    written = datetime.strptime("".join(handoff.groups()), "%Y%m%d%H%M")
                except ValueError:
                    continue
                age = age_days(self.now, written.timestamp())
                if age > HANDOFF_DAYS:
                    self.add(
                        "handoff",
                        where,
                        f"written {int(age)} days ago; resume from it, or delete it once its"
                        " next action is done or dropped",
                    )
                continue
            boxes = [m.group(1) for line in read(task).splitlines() if (m := CHECKBOX.match(line))]
            if not boxes:
                continue
            open_items = boxes.count(" ")
            if not open_items:
                self.add(
                    "finished",
                    where,
                    "every item is done or dropped; run the work-management conclusion and move"
                    " it to ai/archive/<feature>/",
                )
                continue
            age = age_days(self.now, self.last_change(task))
            if age >= IDLE_DAYS:
                self.add(
                    "idle",
                    where,
                    f"{open_items} open items, unchanged for {int(age)} days; resume it, mark"
                    " dropped items `[-]` with a reason, or archive it",
                )

    @staticmethod
    def moved(lane: Path, branch: str, fallback: bool) -> bool:
        """Whether the lane's branch got commits after it was created, so a HEAD inside the
        default branch means merged, not a fresh lane. Without a reflog: `fallback`."""
        if branch:
            log = git(lane, "reflog", "show", "--format=%gs", f"refs/heads/{branch}")
            subjects = [s for s in log.stdout.splitlines() if s]
            if log.returncode == 0 and subjects:
                return any(not s.startswith("branch: Created") for s in subjects)
        return fallback

    def check_worktrees(self) -> None:
        for ws in self.ws:
            lanes = ws.folder / ".worktrees"
            if not lanes.is_dir() or not self.clone_ok(ws):
                continue
            base = self.base_ref(ws)
            tip = git_out(ws.clone, "rev-parse", f"{base}^{{commit}}").strip() if base else ""
            for lane in sorted(p for p in lanes.iterdir() if p.is_dir()):
                try:
                    self.check_lane(ws, lane, base, tip)
                except GardenError as exc:  # one unreadable lane must not hide the others
                    self.errors.append(f"{self.rel(lane)}: {exc}")

    def check_lane(self, ws: Workspace, lane: Path, base: str | None, tip: str) -> None:
        where = self.rel(lane)
        remove = f"`git -C {self.rel(ws.clone)} worktree remove ../.worktrees/{lane.name}`"
        head = git(lane, "rev-parse", "--show-toplevel", "HEAD")
        top, _, sha = head.stdout.strip().partition("\n")
        if head.returncode != 0 or Path(top).resolve() != lane.resolve():
            self.add(
                "worktree",
                where,
                f"not a git checkout; delete the folder and run"
                f" `git -C {self.rel(ws.clone)} worktree prune`",
            )
            return
        branch = git(lane, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
        if base is not None:
            merged = git(ws.clone, "merge-base", "--is-ancestor", sha, base)
            if merged.returncode == 0 and self.moved(lane, branch, sha != tip):
                name = branch or f"detached {sha[:12]}"
                self.add("worktree", where, f"{name} is merged into {base}; {remove}")
                return
        stamp = git_out(lane, "log", "-1", "--format=%ct").strip()
        if not stamp.isdigit():
            raise GardenError(f"git log in {lane}: no commit time")
        age = age_days(self.now, float(stamp))
        if age >= IDLE_DAYS:
            self.add(
                "worktree",
                where,
                f"{branch or 'HEAD'} has had no commit for {int(age)} days; resume it, or {remove}",
            )

    def check_contents(self) -> None:
        for doc, _ in self.living_docs():
            lines = read(doc).splitlines()
            if len(lines) <= CONTENTS_LINES:
                continue
            head = lines[:CONTENTS_HEAD]
            links = sum(1 for line in head if LINK_ITEM.match(line))
            if any(CONTENTS.match(line) for line in head) or links >= 2:
                continue
            self.add(
                "contents",
                self.rel(doc),
                f"{len(lines)} lines and no contents list in its first {CONTENTS_HEAD} lines;"
                " add a `## Contents` list of its sections at the top, or split it"
                " (docs/guidelines/agent_docs.md)",
            )

    def routes(self, source: Path) -> Iterator[Path]:
        """Markdown files a file cites: from its folder, its tree root and the framework."""
        ws = self.workspace_of(source)
        tree = self.root if ws is None else ws.folder
        for _, raw, link in citations(read(source)):
            word = clean(raw, link)
            if word is None or word.endswith("/"):
                continue
            for base in {source.parent, tree, self.root}:
                path = norm(base / word)
                if (
                    path.suffix == ".md"
                    and path.is_relative_to(self.root)
                    and not self.unscanned(path)
                    and path.is_file()
                    and (not self.static or self.is_tracked(path))
                ):
                    yield path

    def check_unrouted(self) -> None:
        homes = [self.root] + [w.folder for w in self.all_ws]
        starts = [h / "AGENTS.md" for h in homes]
        for home in homes:
            for skills in (".claude/skills", ".agents/skills"):
                starts += sorted((home / skills).glob("*/SKILL.md"))
        reached: set[Path] = set()
        queue = [s for s in starts if s.is_file() and (not self.static or self.is_tracked(s))]
        while queue:
            source = queue.pop()
            if source not in reached:
                reached.add(source)
                queue.extend(p for p in self.routes(source) if p not in reached)
        for doc, ws in self.living_docs():
            home = self.root if ws is None else ws.folder
            if not doc.is_relative_to(home / "docs"):
                continue
            if doc not in reached:
                owner = self.workspace_of(doc)
                agents = "AGENTS.md" if owner is None else self.rel(owner.folder / "AGENTS.md")
                self.add(
                    "unrouted",
                    self.rel(doc),
                    "no chain of citations from an AGENTS.md or a skill reaches it; add a"
                    f" route from {agents} or a reachable domain index, or archive it if obsolete",
                )

    def check_map(self) -> None:
        for doc, ws in self.agents_files():
            text = read(doc)
            rows: set[int] = set()
            in_map = False
            for n, line in enumerate(text.splitlines(), 1):
                if line.startswith("#"):
                    in_map = bool(MAP_HEAD.match(line))
                elif in_map and line.lstrip().startswith("|") and not TABLE_RULE.match(line):
                    rows.add(n)
            trees = self.trees(ws)
            # Folders inside the clone are the team's to organise; a row may name one.
            team = ws.clone.relative_to(ws.folder).as_posix() if ws else None
            for n, raw, link in citations(text):
                word = clean(raw, link) if n in rows else None
                if word is None or (team and f"{word.rstrip('/')}/".startswith(f"{team}/")):
                    continue
                found = [doc.parent / word.rstrip("/")] + [base / word for base, _ in trees]
                if word.endswith("/") or any(self.is_dir(norm(p)) for p in found):
                    self.add(
                        "map",
                        f"{self.rel(doc)}:{n}",
                        f"the row points at the folder `{word}`; point it at the doc that holds"
                        " the rules, one row per doc",
                    )

    def deny_rules(self, ws: Workspace) -> Iterator[tuple[str, str]]:
        """(source file:line, raw word) for each Bash deny rule and Codex pattern word."""
        settings = ws.folder / ".claude" / "settings.json"
        if settings.is_file():
            try:
                data = json.loads(read(settings))
            except json.JSONDecodeError as exc:
                raise GardenError(f"{settings}: invalid JSON: {exc}") from exc
            permissions = data.get("permissions", {}) if isinstance(data, dict) else {}
            deny = permissions.get("deny", []) if isinstance(permissions, dict) else []
            for rule in deny if isinstance(deny, list) else []:
                m = (
                    re.fullmatch(r"Bash\((.*)\)", rule, re.DOTALL)
                    if isinstance(rule, str)
                    else None
                )
                for word in m.group(1).split() if m else []:
                    yield self.rel(settings), word
        for rules in sorted((ws.folder / ".codex" / "rules").glob("*.rules")):
            text = read(rules)
            for m in PATTERN.finditer(text):
                depth, end = 0, m.end() - 1
                while end < len(text):
                    depth += {"[": 1, "]": -1}.get(text[end], 0)
                    if depth == 0:
                        break
                    end += 1
                n = text.count("\n", 0, m.start()) + 1
                for s in STRING.finditer(text, m.end(), end):
                    yield f"{self.rel(rules)}:{n}", s.group(1) or s.group(2) or ""

    def check_deny(self) -> None:
        for ws in self.ws:
            prefix = ws.clone.relative_to(ws.folder).as_posix() + "/"
            words = {(src, t) for src, w in self.deny_rules(ws) if (t := deny_target(w, prefix))}
            if not words or not self.clone_ok(ws):
                continue
            base = self.base_ref(ws)
            if base is None:
                raise GardenError(
                    f"{self.rel(ws.clone)} has neither origin/{ws.default_branch} nor"
                    f" {ws.default_branch}; fix default_branch in {ws.name}/workspace.toml"
                )
            for src, path in sorted(words):
                if git(ws.clone, "cat-file", "-e", f"{base}:{path}").returncode != 0:
                    self.add(
                        "deny",
                        src,
                        f"denies `{path}`, which {base} no longer has; point the rule at the"
                        " file's new path (or drop it if the command is gone) so it stays guarded",
                    )

    def check_tools(self) -> None:
        probes = self.root / PROBES
        fix = f"run the maintenance workflow ({MAINTENANCE}), which reruns the probes"
        m = LAST_RUN.search(read(probes)) if probes.is_file() else None
        if m is None:
            state = "has no `Last run: YYYY-MM-DD, claude X, codex-cli Y` line"
            self.add("tools", PROBES, f"{state if probes.is_file() else 'is missing'}; {fix}")
            return
        try:
            ran = date.fromisoformat(m.group(1))
        except ValueError:
            self.add("tools", PROBES, f"`Last run:` date {m.group(1)!r} is invalid; {fix}")
            return
        age = (date.fromtimestamp(self.now) - ran).days
        if age > PROBE_DAYS:
            self.add("tools", PROBES, f"probes last ran {age} days ago ({ran}); {fix}")
        for cli, recorded in (("claude", m.group(2)), ("codex", m.group(3))):
            current = tool_version(cli)
            if current is None:
                self.notes.append(f"tools: `{cli} --version` gave no version; comparison skipped")
            elif current != recorded.rstrip("."):
                self.add("tools", PROBES, f"{cli} is {current}, probes ran on {recorded}; {fix}")

    def check_context(self) -> None:
        for doc, ws in self.living_docs():
            try:
                metadata = context_metadata(read(doc))
            except ValueError as exc:
                self.add("context", self.rel(doc), f"{exc}; follow docs/guidelines/agent_docs.md")
                continue
            if metadata is None or self.static:
                continue
            paths, revision = metadata
            checkout = self.root if ws is None else self.checkout(ws)
            where = self.rel(doc)
            if revision == "unverified":
                self.add(
                    "freshness", where, "not verified; check claims and record the checked commit"
                )
                continue
            try:
                ignored = git(checkout, "check-ignore", "--", *paths)
                if ignored.returncode not in (0, 1):
                    raise GardenError(f"git check-ignore in {checkout}: {ignored.stderr.strip()}")
                if ignored.returncode == 0:
                    self.add(
                        "context",
                        where,
                        "depends_on path is ignored by git; cannot track: "
                        f"{', '.join(ignored.stdout.splitlines())}; drop it from depends_on",
                    )
                    continue
                known = git(checkout, "rev-parse", "--verify", "-q", f"{revision}^{{commit}}")
                if known.returncode == 1:
                    self.add(
                        "freshness",
                        where,
                        f"verified_commit {revision[:12]} is not in {self.rel(checkout)}"
                        " (squash-merged and collected, shallow or not fetched); re-verify"
                        " claims against evidence, then update verified_commit",
                    )
                    continue
                if known.returncode:
                    raise GardenError(
                        f"git rev-parse {revision} in {checkout}: {known.stderr.strip()}"
                    )
                specs = [f":(literal){path}" for path in paths]
                changed: set[str] = set()
                # The working tree and the index (the pending commit) can differ.
                for cached in ((), ("--cached",)):
                    changed.update(
                        git_out(
                            checkout,
                            "diff",
                            *cached,
                            "--name-only",
                            "--no-renames",
                            "--no-ext-diff",
                            "--no-textconv",
                            "-z",
                            revision,
                            "--",
                            *specs,
                        ).split("\0")
                    )
                changed.discard("")
                changed.update(
                    filter(
                        None,
                        git_out(
                            checkout,
                            "ls-files",
                            "--others",
                            "--exclude-standard",
                            "-z",
                            "--",
                            *specs,
                        ).split("\0"),
                    )
                )
                missing = [
                    path
                    for path in paths
                    if not (checkout / path).exists()
                    or git(checkout, "cat-file", "-e", f"{revision}:{path.rstrip('/')}").returncode
                ]
                if missing or changed:
                    affected = sorted(set(missing) | changed)
                    self.add(
                        "freshness",
                        where,
                        f"dependencies changed or missing in {self.rel(checkout)}: "
                        f"{', '.join(affected[:5])}"
                        f"{' (and more)' if len(affected) > 5 else ''};"
                        " recheck claims against evidence, then patch or retain them;"
                        " update verified_commit only after verification",
                    )
            except GardenError as exc:
                self.errors.append(f"{where}: freshness could not be checked: {exc}")

    def check_changes(self, base: str, lane: Path) -> None:
        lane = lane.resolve()
        ws = self.select_target(lane)
        self.ws = [ws]
        checkout = Path(git_out(lane, "rev-parse", "--show-toplevel").strip())
        before = ref_paths(checkout, base)
        after = ref_paths(checkout, "HEAD")
        prefix = ws.clone.relative_to(ws.folder).as_posix() + "/"
        for doc in (d for d, owner in self.living_docs() if owner is ws):
            for n, raw, link in citations(read(doc)):
                word = clean(raw, link)
                if word is None:
                    continue
                path = word.rstrip("/")
                if path.startswith("../"):
                    resolved = norm(doc.parent / path)
                    if not resolved.is_relative_to(ws.clone):
                        continue
                    path = resolved.relative_to(ws.clone).as_posix()
                path = path.removeprefix(prefix)
                if path in before and path not in after:
                    self.add(
                        "changes",
                        f"{self.rel(doc)}:{n}",
                        f"cites `{word}`, which {base} has and the lane's HEAD lacks; update"
                        " the doc to the new path in this same change, or restore the path",
                    )

    def run(self, checks: Iterable[str]) -> None:
        for name in checks:
            try:
                getattr(self, f"check_{name}")()
            except GardenError as exc:
                self.errors.append(f"{name}: {exc}")

    def active(self) -> list[str]:
        lines = []
        for task in self.task_files():
            if HANDOFF.fullmatch(task.name):
                continue
            text = read(task).splitlines()
            if not any(m and m.group(1) == " " for m in map(CHECKBOX.match, text)):
                continue
            target = next((t.strip() for t in text if t.strip().startswith("Target:")), "")
            target = target or "no Target: line; add one naming the checkout and branch"
            lines.append(f"active: {self.rel(task)} ({target})")
        return lines


STATIC = ("paths", "commands", "contents", "unrouted", "map", "context")
FULL = (*STATIC, "tasks", "worktrees", "deny", "tools")


def session_lines(garden: Garden) -> list[str]:
    lines = []
    if garden.findings:
        counts = Counter(f.kind for f in garden.findings)
        parts = ", ".join(f"{kind} {counts[kind]}" for kind in KINDS if counts[kind])
        lines.append(f"garden: {len(garden.findings)} findings ({parts}); {HINT} and offer the fix")
        tools = [f for f in garden.findings if f.kind == "tools"]
        if tools:
            lines.append(f"garden: maintenance due ({tools[0].where}): {tools[0].message}")
    if garden.errors:
        more = f" (+{len(garden.errors) - 1} more)" if len(garden.errors) > 1 else ""
        lines.append(f"garden: could not check: {garden.errors[0]}{more}")
    return lines[:5] + garden.active()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report context rot in living docs.")
    parser.add_argument("--root", type=Path, help="framework root (default: this script's)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--session", action="store_true", help="summary for the session hook")
    mode.add_argument(
        "--static", action="store_true", help="tracked framework structure; no clones"
    )
    mode.add_argument("--changes", metavar="BASE", help="paths BASE has and LANE's HEAD lacks")
    parser.add_argument("--target", type=Path, metavar="LANE", help="product checkout to validate")
    parser.add_argument("--workspace", metavar="NAME", help="limit workspace checks to NAME")
    args = parser.parse_args(argv)
    if args.changes is not None and args.target is None:
        parser.error("--changes BASE requires --target LANE")
    if args.static and args.target is not None:
        parser.error("--static does not read a product checkout; omit --target")
    return args


def framework_root(arg: Path | None) -> Path:
    if arg is not None:
        return find_framework_root(arg)
    return Path(__file__).resolve().parents[2]


def report(garden: Garden) -> int:
    order = {kind: i for i, kind in enumerate(KINDS)}

    def key(f: Finding) -> tuple[int, str, int]:
        path, _, line = f.where.partition(":")
        return order[f.kind], path, int(line) if line.isdigit() else 0

    for f in sorted(garden.findings, key=key):
        print(f.line())
    for note in garden.notes:
        print(f"note: {note}")
    for error in garden.errors:
        print(f"error: {error}", file=sys.stderr)
    if garden.errors:
        print(f"garden: could not run {len(garden.errors)} check(s); fix the errors above")
        return 2
    count = len(garden.findings)
    print(f"garden: {count} finding{'s' if count != 1 else ''}" if count else "garden: no findings")
    return 1 if count else 0


def session(argv: list[str]) -> int:
    """The SessionStart hook: never fails, never blocks."""
    try:
        args = parse_args(argv)
        root = framework_root(args.root)
        only = args.workspace
        if only is None:
            here = workspace_for(Path.cwd(), root)
            only = here.name if here is not None else None
        target = args.target
        if target is None and only is not None:
            top = git(Path.cwd(), "rev-parse", "--show-toplevel")
            if top.returncode == 0 and Path(top.stdout.strip()).resolve() != root:
                target = Path(top.stdout.strip())
        garden = Garden(root, only=only, target=target)
        garden.run(FULL)
        lines = session_lines(garden)
    except (Exception, SystemExit) as exc:
        print(f"garden: could not run ({exc or type(exc).__name__}); {HINT}")
        return 0
    if lines:
        print("\n".join(lines))
    return 0


def main(argv: list[str]) -> int:
    if "--session" in argv:
        return session(argv)
    args = parse_args(argv)
    try:
        garden = Garden(
            framework_root(args.root), static=args.static, only=args.workspace, target=args.target
        )
        if args.static:
            garden.run(STATIC)
        elif args.changes is not None:
            garden.check_changes(args.changes, args.target)
            garden.run(("context",))
        else:
            garden.run(FULL)
    except (GardenError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return report(garden)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
