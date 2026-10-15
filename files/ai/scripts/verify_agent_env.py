"""Verify the framework's agent environment: structural drift no reviewer notices.

Usage: verify_agent_env.py [--root PATH]
    PATH defaults to the framework holding this script (two folders above ai/scripts/).

Contract:
    Reads the framework and each workspace (a direct child folder holding workspace.toml) and
    prints one line per finding, `<path>: <problem>. Fix: <fix>`, then a summary line. It
    never edits, and never scans a workspace's clone, `.worktrees/`, `.venv/` or `.git/`. It checks:
    - required files exist: the core files, every workflow doc, the guidelines and the
      expected skills (both copies), plus each workspace's own files;
    - root AGENTS.md is within 3.5 KB with the required headings in order; each workspace
      AGENTS.md is within 5 KB and ends with `## Docs` then `## Context map`;
    - no CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md or AGENTS.override.md exists;
    - canonical docs have at least 8 non-blank lines and no two are identical;
    - living docs hold no upper-case angle-bracket token, TBD, FIXME or "placeholder
      description" (a line containing `verify: ignore` is skipped);
    - skill parity: both skill roots hold the same names, copies are byte-identical real
      files, frontmatter is valid and the body points at one path that exists;
    - line budgets: ai/project.md 150, decisions.md 250, architecture.md 300, guidelines 200;
    - settings and hooks files parse, wire the SessionStart and format hooks (Claude settings
      also run `cross_review.py hook` on PreToolUse[ExitPlanMode]),
      and every hook command that names a script is anchored and names a script that exists
      (in Claude settings only `${CLAUDE_PROJECT_DIR}` anchors: `git rev-parse` follows a `cd`);
    - each workspace.toml loads through workspace_profile.load_profile.

It cannot judge the meaning of the text: a doc can pass every check and still be wrong,
stale or useless. Reading it is the reviewer's job.

Exit codes: 0 no findings, 1 findings, 2 could not run (root missing or unreadable).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from workspace_profile import ProfileError, find_workspaces, load_profile

PREFIX = "__P__"
EXPECTED_SKILLS = (
    "__P__-work-management",
    "__P__-plan-review",
    "__P__-pre-land-review",
    "__P__-maintenance",
    "__P__-skill-authoring",
    "__P__-workspace-setup",
)
WORKFLOWS = (
    "work-management",
    "plan-review",
    "pre-land-review",
    "maintenance",
    "workspace-setup",
    "cross-review",
)
WORKFLOW_DOCS = tuple(f"docs/agent-workflows/{name}.md" for name in WORKFLOWS)
GUIDELINE_DOCS = (
    "docs/guidelines/skill_authoring.md",
    "docs/guidelines/agent_docs.md",
    "docs/guidelines/context_evaluation.md",
)
SCRIPTS = tuple(
    f"ai/scripts/{name}.py"
    for name in (
        "workspace_profile",
        "check_code",
        "verify_agent_env",
        "garden",
        "format_changed",
        "cross_review",
    )
)
CORE_FILES = (
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    ".claude/settings.json",
    ".codex/hooks.json",
    "ai/project.md",
    "ai/README.md",
    "docs/dev/architecture.md",
    "docs/dev/decisions.md",
    "ai/tasks/.gitkeep",
    "ai/archive/.gitkeep",
)
REQUIRED_FILES = CORE_FILES + WORKFLOW_DOCS + GUIDELINE_DOCS + SCRIPTS
WORKSPACE_FILES = ("AGENTS.md", ".claude/settings.json", "ai/project.md")
SKILL_ROOTS = (".claude/skills", ".agents/skills")

ROOT_AGENTS_MAX = 3584
WORKSPACE_AGENTS_MAX = 5120
ROOT_HEADINGS = (
    "The one rule",
    "Commands",
    "Verify your work",
    "Codebase map",
    "Persistent state",
    "Trust boundary",
    "Conventions",
    "Etiquette",
)
WORKSPACE_TAIL = ("Docs", "Context map")
BUDGETS = {
    "ai/project.md": 150,
    "docs/dev/decisions.md": 250,
    "docs/dev/architecture.md": 300,
}
GUIDELINE_BUDGET = 200
MIN_CANONICAL_LINES = 8

FORBIDDEN_NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.override.md")
SKIP_DIRS = frozenset({".worktrees", ".venv", ".git", "node_modules", "__pycache__"})

PLACEHOLDER_RES = (
    re.compile(r"<[A-Z][A-Z0-9_]*>"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"placeholder description", re.IGNORECASE),
)
IGNORE_MARK = "verify: ignore"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
SKILL_BODY_MAX = 4
YAML_INDICATORS = frozenset("[]{},#&*!|>%@`'\"")
BACKTICK_RE = re.compile(r"`([^`\s]+)`")

HOOK_EVENTS = {"SessionStart": ("garden.py", "--session"), "PostToolUse": ("format_changed.py",)}
REVIEW_HOOK = "ai/scripts/cross_review.py"
REVIEW_HOOK_EVENTS = (("PreToolUse", "ExitPlanMode"),)
PROJECT_ANCHOR = "\x00P"
GIT_ANCHOR = "\x00G"
PROJECT_VAR_RE = re.compile(r"\$\{CLAUDE_PROJECT_DIR\}|\$CLAUDE_PROJECT_DIR\b")
GIT_TOP_RE = re.compile(
    r"\$\(\s*git rev-parse --show-toplevel\s*\)|`\s*git rev-parse --show-toplevel\s*`"
)
SCRIPT_TOKEN_RE = re.compile(r"[^\s;|&()]*\.(?:py|sh)\b")


@dataclass(frozen=True)
class Finding:
    path: str
    problem: str
    fix: str

    def __str__(self) -> str:
        return f"{self.path}: {self.problem}. Fix: {self.fix}"


class Report:
    """Collects findings with paths relative to the framework root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.findings: list[Finding] = []

    def add(self, path: Path | str, problem: str, fix: str) -> None:
        self.findings.append(Finding(self.rel(path), problem, fix))

    def rel(self, path: Path | str) -> str:
        path = Path(path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


# ---------------------------------------------------------------- helpers


def read_text(path: Path, report: Report) -> str | None:
    """Return the file's text, or None (with a finding) when it is not UTF-8.

    OSError propagates: an unreadable file means the verifier could not run.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        report.add(path, "is not valid UTF-8", "re-save it as UTF-8")
        return None


def clone_paths(workspaces: list[Path]) -> frozenset[Path]:
    """Each workspace's clone, as its profile names it (`repo` when the profile does not
    load; check_profiles reports that)."""
    clones = set()
    for ws in workspaces:
        try:
            clones.add(load_profile(ws / "workspace.toml").clone)
        except ProfileError:
            clones.add(ws / "repo")
    return frozenset(clones)


def walk_files(top: Path, skip: frozenset[Path] = frozenset()) -> Iterator[Path]:
    """Yield files under `top`, pruning SKIP_DIRS and the folders in `skip`, and never
    following symlinked folders."""
    for folder, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and Path(folder) / d not in skip)
        for name in sorted(files):
            yield Path(folder) / name


def markdown_files(top: Path) -> list[Path]:
    if not top.is_dir():
        return []
    return [p for p in walk_files(top) if p.suffix == ".md"]


def headings(text: str) -> list[tuple[int, str]]:
    """Return (level, title) for each ATX heading outside fenced code blocks."""
    found: list[tuple[int, str]] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        marker = stripped[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if match:
            found.append((len(match.group(1)), match.group(2)))
    return found


def non_blank(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def language_guides(root: Path) -> list[str]:
    guides = root / "docs" / "guidelines"
    if not guides.is_dir():
        return []
    return sorted(p.name for p in guides.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)


def expected_skills(root: Path) -> list[str]:
    return [*EXPECTED_SKILLS, *(f"{PREFIX}-{lang}" for lang in language_guides(root))]


# ---------------------------------------------------------------- required files


def check_required(root: Path, workspaces: list[Path], report: Report) -> None:
    for rel in REQUIRED_FILES:
        if not (root / rel).is_file():
            report.add(rel, "required file is missing", "create it as the setup prompt describes")
    for ws in workspaces:
        for rel in WORKSPACE_FILES:
            path = ws / rel
            if not path.is_file():
                report.add(path, "required workspace file is missing", "write it (setup step 7)")


def check_forbidden(root: Path, workspaces: list[Path], report: Report) -> None:
    for path in walk_files(root, clone_paths(workspaces)):
        if path.name in FORBIDDEN_NAMES:
            report.add(
                path,
                f"{path.name} must not exist (it stops AGENTS.md loading or overrides it)",
                "move its rules into the nearest AGENTS.md and delete it",
            )


# ---------------------------------------------------------------- AGENTS.md


def check_root_agents(root: Path, report: Report) -> None:
    path = root / "AGENTS.md"
    if not path.is_file():
        return
    size = path.stat().st_size
    if size > ROOT_AGENTS_MAX:
        report.add(
            path,
            f"is {size} bytes, over the {ROOT_AGENTS_MAX}-byte budget",
            "prune rules that no longer prevent a failure, or move detail to a pointed-at doc",
        )
    text = read_text(path, report)
    if text is None:
        return
    found = headings(text)
    if not found or found[0][0] != 1:
        report.add(path, "does not start with an H1 title", "make the first heading `# <name>`")
    required = [h for h in ROOT_HEADINGS if h != "Conventions" or language_guides(root)]
    h2 = [title for level, title in found if level == 2]
    missing = [h for h in required if h not in h2]
    for title in missing:
        report.add(path, f"missing heading `## {title}`", "add it in the required order")
    present = [h for h in required if h in h2]
    order = sorted(present, key=h2.index)
    if order != present:
        report.add(
            path,
            f"headings out of order: {', '.join(order)}",
            f"reorder them as {', '.join(present)}",
        )


def check_workspace_agents(ws: Path, report: Report) -> None:
    path = ws / "AGENTS.md"
    if not path.is_file():
        return
    size = path.stat().st_size
    if size > WORKSPACE_AGENTS_MAX:
        report.add(
            path,
            f"is {size} bytes, over the {WORKSPACE_AGENTS_MAX}-byte budget",
            "keep only what the root does not say and an agent cannot infer",
        )
    text = read_text(path, report)
    if text is None:
        return
    h2 = [title for level, title in headings(text) if level == 2]
    if tuple(h2[-2:]) != WORKSPACE_TAIL:
        report.add(
            path,
            "must end with `## Docs` then `## Context map` as its last two H2 headings",
            "move those two sections to the end, in that order",
        )


# ---------------------------------------------------------------- docs


def canonical_docs(root: Path) -> list[Path]:
    docs = [root / rel for rel in WORKFLOW_DOCS if (root / rel).is_file()]
    workflows = root / "docs" / "agent-workflows"
    docs += [p for p in markdown_files(workflows) if p not in docs]
    docs += markdown_files(root / "docs" / "guidelines")
    return docs


def check_canonical(root: Path, report: Report) -> None:
    seen: dict[str, Path] = {}
    for path in canonical_docs(root):
        text = read_text(path, report)
        if text is None:
            continue
        if non_blank(text) < MIN_CANONICAL_LINES:
            report.add(
                path,
                f"has {non_blank(text)} non-blank lines, under {MIN_CANONICAL_LINES}",
                "restore the procedure; its skill otherwise points at nothing",
            )
        digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
        if digest in seen:
            report.add(
                path,
                f"is identical to {report.rel(seen[digest])}",
                "write this doc's own content; one home per fact",
            )
        else:
            seen[digest] = path


def living_docs(root: Path, workspaces: list[Path]) -> list[Path]:
    docs: list[Path] = []
    for rel in ("AGENTS.md", "README.md", "ai/project.md", "ai/README.md"):
        docs.append(root / rel)
    docs += markdown_files(root / "docs")
    for ws in workspaces:
        docs += [ws / "AGENTS.md", ws / "ai" / "project.md", *markdown_files(ws / "docs")]
    for base in (root, *workspaces):
        for skills in SKILL_ROOTS:
            folder = base / skills
            if folder.is_dir():
                docs += sorted(p for p in folder.glob("*/SKILL.md") if p.is_file())
    return [p for p in docs if p.is_file()]


def check_placeholders(root: Path, workspaces: list[Path], report: Report) -> None:
    for path in living_docs(root, workspaces):
        text = read_text(path, report)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if IGNORE_MARK in line:
                continue
            for pattern in PLACEHOLDER_RES:
                match = pattern.search(line)
                if match:
                    report.add(
                        f"{report.rel(path)}:{number}",
                        f"placeholder {match.group(0)!r} in a living doc",
                        "replace it with the real content, or add `verify: ignore` to the line",
                    )


def check_budgets(root: Path, workspaces: list[Path], report: Report) -> None:
    budgets: list[tuple[Path, int]] = [(root / rel, cap) for rel, cap in BUDGETS.items()]
    budgets += [(ws / "ai" / "project.md", BUDGETS["ai/project.md"]) for ws in workspaces]
    budgets += [(p, GUIDELINE_BUDGET) for p in markdown_files(root / "docs" / "guidelines")]
    for path, cap in budgets:
        if not path.is_file():
            continue
        text = read_text(path, report)
        if text is None:
            continue
        count = len(text.splitlines())
        if count > cap:
            report.add(
                path,
                f"has {count} lines, over its {cap}-line budget",
                "prune entries that are no longer true or move detail elsewhere",
            )


# ---------------------------------------------------------------- skills


def skill_names(folder: Path) -> set[str]:
    if not folder.is_dir():
        return set()
    return {p.name for p in folder.iterdir() if p.is_dir() or p.is_symlink()}


def plain_scalar_error(value: str) -> str | None:
    """Why an unquoted one-line YAML value would fail to parse, or None when it is fine."""
    if ": " in value or value.endswith(":"):
        return "contains `: `"
    if value[:1] in YAML_INDICATORS or value[:2] in ("- ", "? "):
        return f"starts with the YAML indicator {value[0]!r}"
    return None


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str], dict[str, str]] | None:
    """Split a SKILL.md into simple `key: value` frontmatter, body lines and YAML errors.

    Returns None without frontmatter. The errors map a key to why its unquoted value is
    invalid YAML (other tools parse the frontmatter with a real YAML parser).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    fields: dict[str, str] = {}
    errors: dict[str, str] = {}
    key: str | None = None
    for line in lines[1:end]:
        if key is not None and line[:1] in (" ", "\t"):
            fields[key] = f"{fields[key]} {line.strip()}".strip()
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            key = None
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in (">", "|", ">-", "|-"):
            value = ""
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif value and (error := plain_scalar_error(value)):
            errors[key] = error
        fields[key] = value
    return fields, lines[end + 1 :], errors


def check_skill_file(path: Path, name: str, report: Report) -> None:
    text = read_text(path, report)
    if text is None:
        return
    parsed = parse_frontmatter(text)
    if parsed is None:
        report.add(path, "has no frontmatter between `---` lines", "add `name` and `description`")
        return
    fields, body, errors = parsed
    for key, error in errors.items():
        report.add(
            path,
            f"unquoted `{key}` value {error}: invalid YAML",
            "quote the value or remove the colon",
        )
    skill_name = fields.get("name", "")
    if not NAME_RE.match(skill_name) or len(skill_name) > NAME_MAX:
        report.add(
            path,
            f"name {skill_name!r} is not lowercase letters, digits and single hyphens "
            f"(at most {NAME_MAX} characters)",
            "rename the skill",
        )
    if skill_name != name:
        report.add(path, f"name {skill_name!r} differs from its folder {name!r}", "make them equal")
    description = fields.get("description", "")
    if not description:
        report.add(path, "has no description", "add `description: <what>. Use when <triggers>.`")
    elif len(description) > DESCRIPTION_MAX:
        report.add(
            path,
            f"description is {len(description)} characters, over {DESCRIPTION_MAX}",
            "shorten it",
        )
    if description and "Use when" not in description:
        report.add(path, "description lacks `Use when`", "add `Use when <concrete triggers>.`")
    body_lines = [line for line in body if line.strip()]
    if len(body_lines) > SKILL_BODY_MAX:
        report.add(
            path,
            f"body has {len(body_lines)} lines, over {SKILL_BODY_MAX}",
            "keep the body a pointer; move the procedure into the canonical doc",
        )
    targets = [t for t in BACKTICK_RE.findall("\n".join(body_lines)) if "/" in t]
    if len(targets) != 1:
        report.add(
            path,
            f"body cites {len(targets)} paths in backticks, not exactly one",
            "point at the single canonical doc, e.g. `../../../docs/agent-workflows/<name>.md`",
        )
        return
    target = (path.parent / targets[0]).resolve()
    if not target.is_file():
        report.add(path, f"points at {targets[0]}, which does not exist", "fix the path")


def check_skills(base: Path, expected: list[str], report: Report) -> None:
    """Check parity of the two skill roots under `base` (the framework or a workspace)."""
    roots = [base / rel for rel in SKILL_ROOTS]
    for folder in roots:
        if folder.is_symlink():
            report.add(folder, "is a symlink", "replace it with a real folder of real files")
    names = [skill_names(folder) for folder in roots]
    for name in expected:
        if name not in names[0] and name not in names[1]:
            report.add(
                f"{report.rel(base / SKILL_ROOTS[0])}/{name}",
                "expected skill is missing from both skill roots",
                "restore both copies, per docs/guidelines/skill_authoring.md",
            )
    for i, folder in enumerate(roots):
        other = SKILL_ROOTS[1 - i]
        for name in sorted(names[i] - names[1 - i]):
            report.add(
                folder / name,
                f"skill exists only here, not under {other}",
                f"copy it byte-identically to {other}/{name} or delete it",
            )
    for name in sorted(names[0] & names[1]):
        files = [folder / name / "SKILL.md" for folder in roots]
        broken = False
        for folder, file in zip(roots, files, strict=True):
            if (folder / name).is_symlink() or file.is_symlink():
                report.add(
                    file, "is a symlink", "replace it with a real copy (Codex ignores links)"
                )
                broken = True
            elif not file.is_file():
                report.add(file, "is missing", "add SKILL.md to the skill folder")
                broken = True
        if broken:
            continue
        if files[0].read_bytes() != files[1].read_bytes():
            report.add(
                files[1],
                f"differs from {report.rel(files[0])}",
                "make the two copies byte-identical",
            )
        else:
            files = files[:1]  # identical copies: check one so findings are not doubled
        for file in files:
            check_skill_file(file, name, report)


# ---------------------------------------------------------------- hooks


def hook_strings(hook: dict[str, object]) -> list[str]:
    strings: list[str] = []
    for key in ("command", "commandWindows"):
        value = hook.get(key)
        if isinstance(value, str):
            strings.append(value)
    args = hook.get("args")
    if isinstance(args, list):
        strings += [a for a in args if isinstance(a, str)]
    return strings


def runs_review_hook(hooks: object) -> bool:
    """True if a hook passes the exact argument `hook` to ai/scripts/cross_review.py."""
    for hook in hooks if isinstance(hooks, list) else []:
        if isinstance(hook, dict):
            words = [w.strip("\"'") for s in hook_strings(hook) for w in s.split()]
            if "hook" in words and any(w.endswith(REVIEW_HOOK) for w in words):
                return True
    return False


def event_hooks(groups: object) -> list[dict[str, object]] | None:
    """Flatten `[{"matcher": ..., "hooks": [...]}]` into hook dicts, or None if malformed."""
    if not isinstance(groups, list):
        return None
    hooks: list[dict[str, object]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            return None
        for hook in group["hooks"]:
            if not isinstance(hook, dict):
                return None
            hooks.append(hook)
    return hooks


def check_script_refs(
    text: str, project: Path, root: Path, where: Path, report: Report, *, claude: bool
) -> None:
    """Every script a hook string names must be anchored and must exist. In a Claude
    settings file only `${CLAUDE_PROJECT_DIR}` anchors: `git rev-parse --show-toplevel`
    runs in the session's current folder, which is the clone once an agent `cd`s there."""
    marked = PROJECT_VAR_RE.sub(PROJECT_ANCHOR, text)
    marked = GIT_TOP_RE.sub(GIT_ANCHOR, marked)
    marked = marked.replace('"', "").replace("'", "").replace("\\", "/")
    for token in SCRIPT_TOKEN_RE.findall(marked):
        if token.startswith(PROJECT_ANCHOR):
            base, rest = project, token[len(PROJECT_ANCHOR) :]
        elif token.startswith(GIT_ANCHOR):
            if claude:
                report.add(
                    where,
                    f"hook command anchors {token[len(GIT_ANCHOR) :].lstrip('/')} on"
                    " `git rev-parse`, which resolves to the clone after a `cd` into it",
                    "anchor it with ${CLAUDE_PROJECT_DIR}",
                )
                continue
            base, rest = root, token[len(GIT_ANCHOR) :]
        else:
            report.add(
                where,
                f"hook command names {token} without an anchor",
                "prefix it with ${CLAUDE_PROJECT_DIR} (Claude) or "
                "$(git rev-parse --show-toplevel) (Codex)",
            )
            continue
        target = Path(os.path.normpath(base / rest.lstrip("/")))
        if not target.is_file():
            shown = rest.lstrip("/")
            report.add(
                where,
                f"hook names {shown}, which does not exist at {report.rel(target)}",
                "fix the path or restore the script",
            )


def check_hooks_file(
    path: Path, project: Path, root: Path, report: Report, *, claude: bool
) -> None:
    if not path.is_file():
        return
    text = read_text(path, report)
    if text is None:
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        report.add(path, f"is not valid JSON: {exc}", "fix the syntax")
        return
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        report.add(path, "has no `hooks` object", "add the SessionStart and PostToolUse hooks")
        return
    if claude:
        for event, matcher in REVIEW_HOOK_EVENTS:
            groups = hooks.get(event)
            wired = isinstance(groups, list) and any(
                isinstance(group, dict)
                and group.get("matcher") == matcher
                and runs_review_hook(group.get("hooks"))
                for group in groups
            )
            if not wired:
                where = f" with matcher {matcher!r}"
                report.add(
                    path,
                    f"hooks.{event} does not run cross_review.py hook{where}",
                    f"wire `python {REVIEW_HOOK} hook` under hooks.{event}{where}",
                )
    for event, needles in HOOK_EVENTS.items():
        if event not in hooks:
            report.add(path, f"has no hooks.{event}", f"add it, running {' '.join(needles)}")
            continue
        entries = event_hooks(hooks[event])
        if entries is None:
            report.add(
                path,
                f"hooks.{event} is malformed",
                'use [{"matcher": "...", "hooks": [{"type": "command", "command": ...}]}]',
            )
            continue
        joined = " ".join(s for hook in entries for s in hook_strings(hook))
        if not all(needle in joined for needle in needles):
            report.add(path, f"hooks.{event} does not run {' '.join(needles)}", "wire it")
    for groups in hooks.values():
        for hook in event_hooks(groups) or []:
            for string in hook_strings(hook):
                check_script_refs(string, project, root, path, report, claude=claude)


def check_hooks(root: Path, workspaces: list[Path], report: Report) -> None:
    for base in (root, *workspaces):
        check_hooks_file(base / ".claude" / "settings.json", base, root, report, claude=True)
    check_hooks_file(root / ".codex" / "hooks.json", root, root, report, claude=False)


# ---------------------------------------------------------------- workspaces


def check_profiles(workspaces: list[Path], report: Report) -> None:
    for ws in workspaces:
        try:
            load_profile(ws / "workspace.toml")
        except ProfileError as exc:
            report.add(ws / "workspace.toml", f"does not load: {exc}", "fix the profile")


# ---------------------------------------------------------------- main


def verify(root: Path) -> list[Finding]:
    """Run every check over the framework at `root`. OSError means it could not run."""
    report = Report(root)
    workspaces = find_workspaces(root)
    check_required(root, workspaces, report)
    check_forbidden(root, workspaces, report)
    check_root_agents(root, report)
    for ws in workspaces:
        check_workspace_agents(ws, report)
    check_canonical(root, report)
    check_placeholders(root, workspaces, report)
    check_budgets(root, workspaces, report)
    check_skills(root, expected_skills(root), report)
    for ws in workspaces:
        check_skills(ws, [], report)
    check_hooks(root, workspaces, report)
    check_profiles(workspaces, report)
    return report.findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="framework root (default: the framework holding this script)",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"verify_agent_env: could not run: {root} is not a folder", file=sys.stderr)
        return 2
    try:
        findings = verify(root)
    except OSError as exc:
        print(f"verify_agent_env: could not run: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(f"verify_agent_env: {len(findings)} finding(s) in {root}")
        return 1
    print(f"verify_agent_env: ok ({root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
