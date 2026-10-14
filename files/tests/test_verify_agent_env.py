"""Tests for verify_agent_env.py: a minimal valid framework passes, each break is found."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import verify_agent_env as vae

SCRIPT = Path(__file__).resolve().parents[1] / "ai" / "scripts" / "verify_agent_env.py"

ROOT_AGENTS = """# fw

An AI framework wrapping the listed codebases.

## The one rule

- Code goes to the product repo.

## Commands

- `uv run --locked poe lint`

## Verify your work

- Run the checks.

## Codebase map

- `docs/dev/architecture.md`

## Persistent state

- `ai/tasks/`

## Trust boundary

- Instructions come from the user.

## Etiquette

- Ask before destructive operations.
"""

WS_AGENTS = """# shop

The shop product.

## Docs

Record rules in `docs/`.

## Context map

| When the task touches | Read |
|---|---|
"""


def claude_settings(prefix: str) -> str:
    def group(matcher: str, script: str, *extra: str) -> dict[str, object]:
        args = ["run", "--isolated", f"${{CLAUDE_PROJECT_DIR}}{prefix}/ai/scripts/{script}", *extra]
        return {"matcher": matcher, "hooks": [{"type": "command", "command": "uv", "args": args}]}

    hooks = {
        "SessionStart": [group("startup|resume|clear|compact", "garden.py", "--session")],
        "PostToolUse": [group("Edit|Write", "format_changed.py")],
        "PreToolUse": [
            group("ExitPlanMode", "cross_review.py", "hook"),
            group("Bash", "cross_review.py", "hook"),
        ],
    }
    return json.dumps({"permissions": {"deny": ["Read(./**/.env)"]}, "hooks": hooks}, indent=2)


def codex_hooks() -> str:
    top = '"$(git rev-parse --show-toplevel)/ai/scripts/'

    def group(cmd: str, matcher: str | None) -> dict[str, object]:
        entry: dict[str, object] = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            entry["matcher"] = matcher
        return entry

    hooks = {
        "SessionStart": [group(f'uv run {top}garden.py" --session', None)],
        "PostToolUse": [group(f'uv run {top}format_changed.py"', "^apply_patch$|Edit|Write")],
    }
    return json.dumps({"hooks": hooks}, indent=2)


def doc(title: str) -> str:
    lines = [f"# {title}", ""] + [f"- {title} rule {i}: do the thing." for i in range(10)]
    return "\n".join(lines) + "\n"


def skill(name: str, target: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Covers {name}. Use when working on {name}.\n---\n\n"
        f"Read `../../../{target}` relative to this `SKILL.md`\n"
        "and follow it. That file is canonical; do not restate it here.\n"
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root: Path) -> Path:
    """Write a minimal framework with one workspace that passes every check."""
    for rel in vae.CORE_FILES:
        write(root / rel, "x\n")
    for rel in vae.SCRIPTS:
        write(root / rel, '"""stub"""\n')
    write(root / "AGENTS.md", ROOT_AGENTS)
    write(root / ".claude/settings.json", claude_settings(""))
    write(root / ".codex/hooks.json", codex_hooks())
    for rel in (*vae.WORKFLOW_DOCS, *vae.GUIDELINE_DOCS):
        write(root / rel, doc(rel))
    targets = dict(zip(vae.EXPECTED_SKILLS, vae.WORKFLOW_DOCS, strict=False))
    targets["__P__-skill-authoring"] = "docs/guidelines/skill_authoring.md"
    targets["__P__-workspace-setup"] = "docs/agent-workflows/workspace-setup.md"
    for name in vae.EXPECTED_SKILLS:
        for skills in vae.SKILL_ROOTS:
            write(root / skills / name / "SKILL.md", skill(name, targets[name]))
    ws = root / "shop"
    write(ws / "workspace.toml", 'name = "shop"\nremote = "https://example.com/shop.git"\n')
    write(ws / "AGENTS.md", WS_AGENTS)
    write(ws / "ai/project.md", "# shop facts\n")
    write(ws / ".claude/settings.json", claude_settings("/.."))
    return root


@pytest.fixture
def fw(tmp_path: Path) -> Path:
    return build(tmp_path / "fw")


def problems(root: Path) -> list[str]:
    return [str(f) for f in vae.verify(root)]


def assert_one(root: Path, *needles: str) -> None:
    found = problems(root)
    hits = [f for f in found if all(n in f for n in needles)]
    assert hits, f"no finding with {needles}; got {found}"


def test_valid_framework_passes(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert vae.main(["--root", str(fw)]) == 0
    assert "ok" in capsys.readouterr().out


def test_findings_exit_1(fw: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (fw / "README.md").unlink()
    assert vae.main(["--root", str(fw)]) == 1
    out = capsys.readouterr().out
    assert "README.md: required file is missing. Fix:" in out
    assert "1 finding(s)" in out


def test_missing_root_exits_2(tmp_path: Path) -> None:
    assert vae.main(["--root", str(tmp_path / "nope")]) == 2


def test_unreadable_file_exits_2(fw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root: Path) -> list[vae.Finding]:
        raise PermissionError("denied")

    monkeypatch.setattr(vae, "verify", boom)
    assert vae.main(["--root", str(fw)]) == 2


def test_runs_standalone(fw: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(fw)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deleted_workflow_doc(fw: Path) -> None:
    (fw / "docs/agent-workflows/plan-review.md").unlink()
    assert_one(fw, "docs/agent-workflows/plan-review.md", "required file is missing")
    assert_one(fw, "__P__-plan-review/SKILL.md", "does not exist")


def test_missing_workspace_file(fw: Path) -> None:
    (fw / "shop/ai/project.md").unlink()
    assert_one(fw, "shop/ai/project.md", "required workspace file")


def test_both_skill_copies_deleted(fw: Path) -> None:
    for skills in vae.SKILL_ROOTS:
        shutil.rmtree(fw / skills / "__P__-maintenance")
    assert_one(fw, "__P__-maintenance", "missing from both")


def test_one_skill_copy_deleted(fw: Path) -> None:
    shutil.rmtree(fw / ".agents/skills/__P__-maintenance")
    assert_one(fw, ".claude/skills/__P__-maintenance", "only here")


def test_skill_folder_without_skill_md(fw: Path) -> None:
    (fw / ".agents/skills/__P__-maintenance/SKILL.md").unlink()
    assert_one(fw, ".agents/skills/__P__-maintenance/SKILL.md", "is missing")


def test_unequal_skill_copies(fw: Path) -> None:
    path = fw / ".agents/skills/__P__-maintenance/SKILL.md"
    write(path, path.read_text() + "\n")
    assert_one(fw, ".agents/skills/__P__-maintenance/SKILL.md", "differs from")


def test_symlinked_skill_file(fw: Path) -> None:
    path = fw / ".agents/skills/__P__-maintenance/SKILL.md"
    path.unlink()
    path.symlink_to(fw / ".claude/skills/__P__-maintenance/SKILL.md")
    assert_one(fw, ".agents/skills/__P__-maintenance/SKILL.md", "is a symlink")


def test_symlinked_skill_folder(fw: Path) -> None:
    folder = fw / ".agents/skills/__P__-maintenance"
    shutil.rmtree(folder)
    folder.symlink_to(fw / ".claude/skills/__P__-maintenance")
    assert_one(fw, ".agents/skills/__P__-maintenance/SKILL.md", "is a symlink")


def test_symlinked_skill_root(fw: Path) -> None:
    shutil.rmtree(fw / ".agents/skills")
    (fw / ".agents/skills").symlink_to(fw / ".claude/skills")
    assert_one(fw, ".agents/skills", "is a symlink")


def set_skill(root: Path, name: str, text: str) -> None:
    for skills in vae.SKILL_ROOTS:
        write(root / skills / name / "SKILL.md", text)


def test_bad_skill_name(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text.replace("name: __P__-maintenance", "name: FW--Maint"))
    assert_one(fw, "__P__-maintenance/SKILL.md", "single hyphens")
    assert_one(fw, "__P__-maintenance/SKILL.md", "differs from its folder")


def test_missing_use_when(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text.replace("Use when", "For"))
    assert_one(fw, "__P__-maintenance/SKILL.md", "lacks `Use when`")


def test_long_and_missing_description(fw: Path) -> None:
    long = "x" * 1100 + " Use when y."
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text.replace("description: ", f"description: {long} "))
    assert_one(fw, "__P__-maintenance/SKILL.md", "over 1024")
    bare = "---\nname: __P__-maintenance\n---\nRead `../../../AGENTS.md`.\n"
    set_skill(fw, "__P__-maintenance", bare)
    assert_one(fw, "__P__-maintenance/SKILL.md", "has no description")


def test_unquoted_colon_in_description(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text.replace("Covers", "Upkeep: garden. Covers"))
    assert_one(fw, "__P__-maintenance/SKILL.md", "contains `: `", "quote the value")


def test_quoted_and_folded_description_pass(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    quoted = text.replace("description: Covers", 'description: "Upkeep: Covers').replace(
        "__P__-maintenance.\n---", '__P__-maintenance."\n---'
    )
    set_skill(fw, "__P__-maintenance", quoted)
    assert problems(fw) == []
    folded = text.replace("description: Covers", "description: >\n  Upkeep: Covers")
    set_skill(fw, "__P__-maintenance", folded)
    assert problems(fw) == []


def test_yaml_indicator_in_description(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text.replace("description: Covers", "description: *Covers"))
    assert_one(fw, "__P__-maintenance/SKILL.md", "YAML indicator")


def test_no_frontmatter(fw: Path) -> None:
    set_skill(fw, "__P__-maintenance", "Read `../../../AGENTS.md`.\n")
    assert_one(fw, "__P__-maintenance/SKILL.md", "no frontmatter")
    set_skill(fw, "__P__-maintenance", "---\nname: __P__-maintenance\n")
    assert_one(fw, "__P__-maintenance/SKILL.md", "no frontmatter")


def test_skill_body_too_long_and_two_paths(fw: Path) -> None:
    text = skill("__P__-maintenance", "docs/agent-workflows/maintenance.md")
    set_skill(fw, "__P__-maintenance", text + "a\nb\nc `x/y.md`\n")
    assert_one(fw, "__P__-maintenance/SKILL.md", "over 4")
    assert_one(fw, "__P__-maintenance/SKILL.md", "cites 2 paths")


def test_identical_skill_copies_report_once(fw: Path) -> None:
    set_skill(fw, "__P__-maintenance", "Read `../../../AGENTS.md`.\n")
    hits = [f for f in problems(fw) if "no frontmatter" in f]
    assert len(hits) == 1, hits


def test_paired_extra_skill_is_fine(fw: Path) -> None:
    set_skill(fw, "__P__-shop-release", skill("__P__-shop-release", "AGENTS.md"))
    assert problems(fw) == []


def test_language_guide_requires_conventions_and_skill(fw: Path) -> None:
    write(fw / "docs/guidelines/python/style.md", doc("python style"))
    assert_one(fw, "AGENTS.md", "missing heading `## Conventions`")
    assert_one(fw, "__P__-python", "missing from both")
    agents = (fw / "AGENTS.md").read_text()
    write(fw / "AGENTS.md", agents.replace("## Etiquette", "## Conventions\n\n- x\n\n## Etiquette"))
    set_skill(fw, "__P__-python", skill("__P__-python", "docs/guidelines/python/style.md"))
    assert problems(fw) == []


def test_oversized_root_agents(fw: Path) -> None:
    write(fw / "AGENTS.md", ROOT_AGENTS + "<!-- pad -->\n" * 300)
    assert_one(fw, "AGENTS.md", "over the 3072-byte budget")


def test_oversized_workspace_agents(fw: Path) -> None:
    write(fw / "shop/AGENTS.md", "pad\n" * 1300 + WS_AGENTS)
    assert_one(fw, "shop/AGENTS.md", "over the 5120-byte budget")


def test_headings_out_of_order(fw: Path) -> None:
    text = ROOT_AGENTS.replace("## Commands", "## TMP").replace("## Etiquette", "## Commands")
    write(fw / "AGENTS.md", text.replace("## TMP", "## Etiquette"))
    assert_one(fw, "AGENTS.md", "headings out of order")


def test_missing_h1_and_heading(fw: Path) -> None:
    write(fw / "AGENTS.md", ROOT_AGENTS.replace("# fw\n", "").replace("## Codebase map", "## Map"))
    assert_one(fw, "AGENTS.md", "H1")
    assert_one(fw, "AGENTS.md", "missing heading `## Codebase map`")


def test_heading_in_fence_ignored(fw: Path) -> None:
    write(
        fw / "AGENTS.md", ROOT_AGENTS.replace("## Commands", "```\n## Etiquette\n```\n## Commands")
    )
    assert problems(fw) == []


def test_workspace_agents_tail(fw: Path) -> None:
    write(fw / "shop/AGENTS.md", WS_AGENTS + "\n## Extra\n")
    assert_one(fw, "shop/AGENTS.md", "must end with `## Docs`")


@pytest.mark.parametrize(
    "rel", ["CLAUDE.md", ".claude/CLAUDE.md", "shop/CLAUDE.local.md", "docs/AGENTS.override.md"]
)
def test_forbidden_file(fw: Path, rel: str) -> None:
    write(fw / rel, "x\n")
    assert_one(fw, rel, "must not exist")


def test_repo_and_worktrees_ignored(fw: Path) -> None:
    write(fw / "shop/repo/CLAUDE.md", "<PLACEHOLDER>\n")
    write(fw / "shop/.worktrees/a/CLAUDE.md", "x\n")
    write(fw / "shop/repo/docs/x.md", "TBD\n")
    assert problems(fw) == []


def test_folder_named_repo_outside_a_workspace_is_scanned(fw: Path) -> None:
    write(fw / "docs/repo/CLAUDE.md", "x\n")
    assert_one(fw, "docs/repo/CLAUDE.md", "must not exist")


def test_custom_clone_path_ignored(fw: Path) -> None:
    write(fw / "shop/workspace.toml", 'name = "shop"\nremote = "x"\nclone = "code"\n')
    write(fw / "shop/code/CLAUDE.md", "x\n")
    assert problems(fw) == []


def test_nested_clone_path_ignored(fw: Path) -> None:
    write(fw / "shop/workspace.toml", 'name = "shop"\nremote = "x"\nclone = "src/code"\n')
    write(fw / "shop/src/code/CLAUDE.md", "x\n")
    assert problems(fw) == []


def test_profile_outside_a_workspace_does_not_hide_its_repo(fw: Path) -> None:
    write(fw / "docs/examples/workspace.toml", 'name = "examples"\nremote = "x"\n')
    write(fw / "docs/examples/repo/CLAUDE.md", "x\n")
    assert_one(fw, "docs/examples/repo/CLAUDE.md", "must not exist")


def test_empty_canonical_doc(fw: Path) -> None:
    write(fw / "docs/agent-workflows/maintenance.md", "# m\n\n")
    assert_one(fw, "docs/agent-workflows/maintenance.md", "non-blank lines")


def test_truncated_workflow_doc(fw: Path) -> None:
    write(fw / "docs/agent-workflows/maintenance.md", "# Maintenance\n\nSee below.\n")
    assert_one(fw, "docs/agent-workflows/maintenance.md", "2 non-blank lines, under 8")


def test_kit_canonical_docs_are_not_truncated() -> None:
    kit = Path(__file__).resolve().parents[1]
    short = [
        (p.name, vae.non_blank(p.read_text(encoding="utf-8")))
        for p in vae.canonical_docs(kit)
        if p.is_relative_to(kit / "docs" / "agent-workflows")
        and vae.non_blank(p.read_text(encoding="utf-8")) < vae.MIN_CANONICAL_LINES
    ]
    assert short == []


def test_truncated_guideline_doc(fw: Path) -> None:
    write(fw / "docs/guidelines/agent_docs.md", "# Docs\n\nPreserve decision rationale.\n")
    assert_one(fw, "docs/guidelines/agent_docs.md", "non-blank lines")


def test_duplicate_canonical_docs(fw: Path) -> None:
    shutil.copy(fw / "docs/agent-workflows/maintenance.md", fw / "docs/guidelines/agent_docs.md")
    assert_one(fw, "docs/guidelines/agent_docs.md", "identical to")


@pytest.mark.parametrize(
    ("rel", "line"),
    [
        ("README.md", "Product: <PRODUCT>"),
        ("shop/ai/project.md", "TBD later"),
        ("docs/dev/decisions.md", "FIXME"),
        (".claude/skills/__P__-maintenance/SKILL.md", "Placeholder Description"),
    ],
)
def test_placeholder(fw: Path, rel: str, line: str) -> None:
    path = fw / rel
    write(path, path.read_text() + line + "\n")
    assert_one(fw, rel, "placeholder")


def test_lowercase_template_and_ignore_mark_pass(fw: Path) -> None:
    path = fw / "docs/guidelines/skill_authoring.md"
    extra = "Name it `<p>-<name>` on `<base>`.\nNo `<PLACEHOLDER>` remains. verify: ignore\n"
    write(path, path.read_text() + extra)
    assert problems(fw) == []


def test_non_utf8_doc(fw: Path) -> None:
    (fw / "README.md").write_bytes(b"\xff\xfe bad")
    assert_one(fw, "README.md", "not valid UTF-8")


@pytest.mark.parametrize(
    ("rel", "lines"),
    [
        ("ai/project.md", 151),
        ("shop/ai/project.md", 151),
        ("docs/dev/decisions.md", 251),
        ("docs/dev/architecture.md", 301),
        ("docs/guidelines/agent_docs.md", 201),
    ],
)
def test_budget_overrun(fw: Path, rel: str, lines: int) -> None:
    write(fw / rel, "".join(f"line {i}\n" for i in range(lines)))
    assert_one(fw, rel, f"has {lines} lines")


@pytest.mark.parametrize("rel", [".claude/settings.json", "shop/.claude/settings.json"])
def test_bad_json(fw: Path, rel: str) -> None:
    write(fw / rel, "{not json")
    assert_one(fw, rel, "not valid JSON")


def test_missing_hooks(fw: Path) -> None:
    write(fw / ".claude/settings.json", "{}")
    write(fw / ".codex/hooks.json", '{"hooks": {"SessionStart": [{"hooks": "x"}]}}')
    assert_one(fw, ".claude/settings.json", "no `hooks` object")
    assert_one(fw, ".codex/hooks.json", "hooks.SessionStart is malformed")
    assert_one(fw, ".codex/hooks.json", "no hooks.PostToolUse")


def test_hook_not_wired(fw: Path) -> None:
    text = (fw / ".codex/hooks.json").read_text().replace("--session", "")
    write(fw / ".codex/hooks.json", text)
    assert_one(fw, ".codex/hooks.json", "hooks.SessionStart does not run garden.py --session")


@pytest.mark.parametrize(("index", "matcher"), [(0, "ExitPlanMode"), (1, "Bash")])
def test_review_hook_needs_hook_argument(fw: Path, index: int, matcher: str) -> None:
    data = json.loads((fw / ".claude/settings.json").read_text())
    for hook in data["hooks"]["PreToolUse"][index]["hooks"]:
        hook["args"] = [a for a in hook["args"] if a != "hook"] + ["status"]
    write(fw / ".claude/settings.json", json.dumps(data))
    message = f"hooks.PreToolUse does not run cross_review.py hook with matcher {matcher!r}"
    assert_one(fw, ".claude/settings.json", message)


@pytest.mark.parametrize(("index", "matcher"), [(0, "ExitPlanMode"), (1, "Bash")])
def test_review_hook_needs_its_matcher(fw: Path, index: int, matcher: str) -> None:
    data = json.loads((fw / ".claude/settings.json").read_text())
    data["hooks"]["PreToolUse"][index]["matcher"] = "Edit"
    write(fw / ".claude/settings.json", json.dumps(data))
    message = f"hooks.PreToolUse does not run cross_review.py hook with matcher {matcher!r}"
    assert_one(fw, ".claude/settings.json", message)


def test_unanchored_hook(fw: Path) -> None:
    text = (fw / ".claude/settings.json").read_text()
    write(fw / ".claude/settings.json", text.replace("${CLAUDE_PROJECT_DIR}/", ""))
    assert_one(fw, ".claude/settings.json", "ai/scripts/garden.py without an anchor")


def test_unanchored_command_windows(fw: Path) -> None:
    data = json.loads((fw / ".claude/settings.json").read_text())
    data["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] = "python ai\\scripts\\x.py"
    write(fw / ".claude/settings.json", json.dumps(data))
    assert_one(fw, ".claude/settings.json", "ai/scripts/x.py without an anchor")


def test_claude_hook_anchored_on_git_rev_parse(fw: Path) -> None:
    text = (fw / "shop/.claude/settings.json").read_text()
    write(
        fw / "shop/.claude/settings.json",
        text.replace("${CLAUDE_PROJECT_DIR}/..", "$(git rev-parse --show-toplevel)"),
    )
    assert_one(fw, "shop/.claude/settings.json", "resolves to the clone")


def test_hook_names_missing_script(fw: Path) -> None:
    (fw / "ai/scripts/format_changed.py").unlink()
    assert_one(fw, ".claude/settings.json", "format_changed.py, which does not exist")
    assert_one(fw, "shop/.claude/settings.json", "format_changed.py, which does not exist")
    assert_one(fw, ".codex/hooks.json", "format_changed.py, which does not exist")


def test_workspace_hook_without_parent_prefix(fw: Path) -> None:
    write(fw / "shop/.claude/settings.json", claude_settings(""))
    assert_one(fw, "shop/.claude/settings.json", "does not exist at shop/ai/scripts/garden.py")


def test_bad_workspace_toml(fw: Path) -> None:
    write(fw / "shop/workspace.toml", 'name = "other"\nremote = "x"\n')
    assert_one(fw, "shop/workspace.toml", "does not load")
