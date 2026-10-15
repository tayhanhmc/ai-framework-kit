"""Install the kit's files/ into a framework, or update an earlier install, recording the kit
revision so the next update can tell the user's edits from the kit's.

Usage:
    uv run --isolated --no-config --no-project --python ">=3.11" <kit>/install.py \
        --framework PATH [--prefix P] [--revision REV] [--dry-run]

Contract:
    - Source: the kit's committed `files/` at `--revision` (default HEAD), read with
      `git ls-tree` and `git cat-file`. `__P__` becomes the prefix in paths and in text.
      Kit files must be plain, non-executable UTF-8 text; anything else is refused. When
      installing HEAD, uncommitted or untracked changes in `files/` are refused: the
      revision would not reproduce the install. To install work in progress, commit it, or
      pass a snapshot commit (check_kit.sh shows how to make one without touching the kit).
    - Record: `.kit-install.toml` at the framework root holds the kit revision and the
      prefix. It is written last and each file is replaced atomically, so an interrupted
      run leaves the previous record and a rerun completes the install.
    - Each kit file is compared with the framework's copy (ours), the kit at the recorded
      revision (base) and the kit now (new):
        ours equals new                       nothing
        ours absent, not in base              create
        ours equals base                      update
        ours differs from base                three-way `git merge-file`; a clean merge is
                                              written, an overlap is a conflict
        ours absent, in base (deleted here)   left deleted if the kit did not change it,
                                              else a conflict
        no base (no record, or a path new     a conflict when ours differs from new
          to the kit)
      A conflict never touches the framework's file. It writes
      `<file>.kit-conflict-<rev>` beside it: the conflict-marked merge, or the kit's
      version when there is no base. Resolve by hand, then delete the sidecar; while one
      from a finished run exists, the installer changes nothing and exits 1.
    - A file the kit dropped is removed when it equals base, else kept and reported.
    - Files the kit does not ship (AGENTS.md, docs the setup writes, workspaces) are never
      read or written, and git never runs in the framework.
    - `--dry-run` prints the plan with diffs and writes nothing.

Exit codes:
    0  installed, or nothing to change (dry run: nothing would change)
    1  conflicts or kept files to review (dry run: changes pending)
    2  could not run: git or uv missing, bad arguments, kit not a git checkout, dirty kit,
       unsupported kit file, recorded revision missing from the kit's history, unreadable
       record, or an I/O error. Never a pass.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

RECORD = ".kit-install.toml"
CONFLICT = ".kit-conflict-"
TMP_SUFFIX = ".kit-tmp"
TOKEN = "__P__"
PREFIX = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class CannotRun(Exception):
    """The installer cannot run; exit 2."""


@dataclass(frozen=True)
class Record:
    revision: str
    prefix: str


@dataclass(frozen=True)
class Step:
    action: str  # create, update, merge, remove, conflict, keep, skip
    path: str
    ours: bytes | None = None
    data: bytes | None = None  # the new file, or the sidecar for a conflict
    note: str = ""


def say(message: str) -> None:
    print(f"install: {message}")


def git(kit: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(kit), *args], input=stdin, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise CannotRun(f"git {' '.join(args)} failed in {kit}: {detail}")
    return result.stdout


def tree_files(kit: Path, revision: str) -> dict[str, bytes]:
    """`files/` as committed at `revision`, keyed by path below `files/`."""
    root = git(kit, "rev-parse", "--show-prefix").decode().strip() + "files/"
    entries = []
    for line in git(kit, "ls-tree", "-r", "-z", "--full-tree", revision, "--", root).split(b"\0"):
        if not line:
            continue
        meta, raw = line.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = raw.decode()
        if kind != "blob" or mode != "100644":
            raise CannotRun(f"unsupported kit entry {path} ({mode} {kind}); ship plain files only")
        entries.append((path[len(root) :], sha))
    batch = git(kit, "cat-file", "--batch", stdin="".join(f"{s}\n" for _, s in entries).encode())
    files, at = {}, 0
    for path, sha in entries:
        header_end = batch.index(b"\n", at)
        got, _, size_text = batch[at:header_end].decode().split()
        if got != sha:
            raise CannotRun(f"git cat-file returned {got} for {sha}")
        size = int(size_text)
        start = header_end + 1
        files[path] = batch[start : start + size]
        at = start + size + 1
    return files


def render(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    """Replace the prefix token in paths and in text; kit files must be UTF-8 text."""
    out = {}
    for path, data in files.items():
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CannotRun(f"kit file {path} is not UTF-8 text; ship text files only") from exc
        out[path.replace(TOKEN, prefix)] = text.replace(TOKEN, prefix).encode("utf-8")
    return out


def read_record(framework: Path) -> Record | None:
    path = framework / RECORD
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        record = Record(data["kit_revision"], data["prefix"])
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise CannotRun(f"cannot read {path}: {exc}") from exc
    if not (
        isinstance(record.revision, str)
        and REVISION.fullmatch(record.revision)
        and isinstance(record.prefix, str)
        and PREFIX.fullmatch(record.prefix)
    ):
        raise CannotRun(f"{path} holds unexpected values; restore it from the framework's git")
    return record


def record_text(record: Record) -> bytes:
    # The pragma stops detect-secrets reading the revision as a high-entropy secret.
    return (
        "# Written by the kit's install.py, which reads it to update this framework.\n"
        "# Do not edit; rerun the installer.\n"
        f'kit_revision = "{record.revision}"  # pragma: allowlist secret\n'
        f'prefix = "{record.prefix}"\n'
    ).encode()


def merge3(ours: bytes, base: bytes, new: bytes, labels: tuple[str, str]) -> tuple[bytes, bool]:
    """`git merge-file` of ours, base and new: (result, clean)."""
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for name, data in (("ours", ours), ("base", base), ("new", new)):
            Path(tmp, name).write_bytes(data)
            names.append(str(Path(tmp, name)))
        label_args = ["-L", "framework", "-L", labels[0], "-L", labels[1]]
        command = ["git", "merge-file", "-p", "--diff3", *label_args, *names]
        result = subprocess.run(command, capture_output=True)
    if result.returncode >= 128:
        raise CannotRun(f"git merge-file failed: {result.stderr.decode(errors='replace')}")
    return result.stdout, result.returncode == 0


def plan(
    framework: Path,
    base: dict[str, bytes] | None,
    new: dict[str, bytes],
    labels: tuple[str, str],
) -> list[Step]:
    steps = []
    for path in sorted(set(new) | set(base or {})):
        dest = framework / path
        if dest.is_dir():
            raise CannotRun(f"{dest} is a folder; the kit ships a file there")
        ours = dest.read_bytes() if dest.exists() else None
        old = base.get(path) if base is not None else None
        kit = new.get(path)
        if kit is None:
            assert old is not None
            if ours == old:
                steps.append(Step("remove", path, ours))
            elif ours is not None:
                steps.append(Step("keep", path, ours, note="the kit dropped it; you changed it"))
            continue
        if ours == kit:
            continue
        if ours is None:
            if old is None:
                steps.append(Step("create", path, None, kit))
            elif old == kit:
                steps.append(Step("skip", path, note="deleted here; the kit has not changed it"))
            else:
                note = "deleted here, changed in the kit; the sidecar holds the kit's version"
                steps.append(Step("conflict", path, None, kit, note))
        elif old is None:
            note = "differs from the kit, with no recorded base; the sidecar holds the kit's"
            steps.append(Step("conflict", path, ours, kit, note + " version"))
        elif ours == old:
            steps.append(Step("update", path, ours, kit))
        else:
            merged, clean = merge3(ours, old, kit, labels)
            if clean and merged != ours:
                steps.append(Step("merge", path, ours, merged))
            elif not clean:
                note = "your edits and the kit's overlap; the sidecar holds the marked merge"
                steps.append(Step("conflict", path, ours, merged, note))
    return steps


def sidecars(framework: Path, path: str) -> list[Path]:
    dest = framework / path
    return sorted(dest.parent.glob(glob.escape(dest.name) + CONFLICT + "*"))


def file_mode(dest: Path) -> int:
    """Keep an existing file's mode; a new one gets the usual 0666 less the umask."""
    if dest.exists():
        return dest.stat().st_mode & 0o7777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_atomic(dest: Path, data: bytes, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove(framework: Path, path: str) -> None:
    dest = framework / path
    dest.unlink()
    parent = dest.parent
    while parent != framework and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def show(step: Step, target: str) -> None:
    sidecar = f" -> {step.path}{CONFLICT}{target}" if step.action == "conflict" else ""
    note = f" ({step.note})" if step.note else ""
    print(f"{step.action:8} {step.path}{sidecar}{note}")


def show_diff(step: Step) -> None:
    before, after = step.ours or b"", step.data or b""
    if step.action == "remove":
        after = b""
    lines = difflib.unified_diff(
        before.decode("utf-8", errors="replace").splitlines(keepends=True),
        after.decode("utf-8", errors="replace").splitlines(keepends=True),
        f"a/{step.path}",
        f"b/{step.path}" if step.action != "conflict" else f"kit/{step.path}",
    )
    sys.stdout.writelines(line if line.endswith("\n") else line + "\n" for line in lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--framework", required=True, type=Path, help="framework root folder")
    parser.add_argument("--prefix", help="skill prefix; required on the first install")
    parser.add_argument("--revision", default="HEAD", help="kit commit to install (HEAD)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    return parser.parse_args(argv)


def install(args: argparse.Namespace) -> int:
    for tool in ("git", "uv"):
        if shutil.which(tool) is None:
            raise CannotRun(f"{tool} is not on PATH; install it and rerun")
    kit = Path(__file__).resolve().parent
    framework = args.framework.expanduser().resolve()
    if not framework.is_dir():
        raise CannotRun(f"framework folder {framework} does not exist; create it first")
    if not (kit / "files").is_dir():
        raise CannotRun(f"{kit} holds no files/ folder; run the installer from the kit")
    revision = git(kit, "rev-parse", "--verify", f"{args.revision}^{{commit}}").decode().strip()
    if args.revision == "HEAD" and git(
        kit, "status", "--porcelain", "--untracked-files=all", "--", "files/"
    ):
        raise CannotRun(
            f"the kit's files/ has uncommitted changes, so revision {revision[:12]} would not "
            "reproduce this install; commit them, or pass --revision <snapshot commit>"
        )

    record = read_record(framework)
    prefix = args.prefix or (record.prefix if record else None)
    if prefix is None:
        raise CannotRun("no recorded install here; pass --prefix for the first install")
    if not PREFIX.fullmatch(prefix):
        raise CannotRun(f"prefix {prefix!r} must be lower-case letters, digits and hyphens")
    if record and record.prefix != prefix:
        raise CannotRun(
            f"this framework was installed with prefix {record.prefix!r}; "
            "changing the prefix is not supported"
        )

    new = render(tree_files(kit, revision), prefix)
    base: dict[str, bytes] | None = None
    if record:
        try:
            git(kit, "cat-file", "-e", f"{record.revision}^{{commit}}")
        except CannotRun as exc:
            raise CannotRun(
                f"recorded kit revision {record.revision} is not in {kit}'s history; "
                "fetch it (git fetch) or check out the kit this framework came from"
            ) from exc
        base = render(tree_files(kit, record.revision), prefix)

    target = revision[:12]
    previous = record.revision[:12] if record else None
    managed = sorted(set(new) | set(base or {}))
    leftovers: list[Path] = []
    unresolved: list[Path] = []
    for path in managed:
        for sidecar in sidecars(framework, path):
            # A sidecar for this target before the record says so is from an interrupted run.
            fresh = sidecar.name.endswith(CONFLICT + target) and previous != target
            (leftovers if fresh else unresolved).append(sidecar)
    if unresolved:
        for sidecar in unresolved:
            print(f"conflict {sidecar.relative_to(framework)} (unresolved from an earlier run)")
        say("resolve each conflict, delete its sidecar, then rerun; nothing was changed")
        return 1

    base_label = f"kit {previous}" if previous else "kit (none)"
    steps = plan(framework, base, new, (base_label, f"kit {target}"))
    new_record = record_text(Record(revision, prefix))
    record_path = framework / RECORD
    record_changes = not record_path.exists() or record_path.read_bytes() != new_record
    for step in steps:
        show(step, target)
        if args.dry_run and step.action not in ("keep", "skip"):
            show_diff(step)
    actions = ("create", "update", "merge", "remove", "conflict", "keep")
    counts = {a: sum(s.action == a for s in steps) for a in actions}
    summary = ", ".join(f"{n} {a}" for a, n in counts.items() if n) or "no file changes"
    review = counts["conflict"] + counts["keep"]

    if args.dry_run:
        changes = any(s.action != "skip" for s in steps) or record_changes
        say(f"dry run, kit {target} into {framework}: {summary}; nothing written")
        return 1 if changes else 0

    for path in managed:
        for stale in (framework / path).parent.glob(
            f".{glob.escape(Path(path).name)}.*{TMP_SUFFIX}"
        ):
            stale.unlink()
    for step in steps:
        dest = framework / step.path
        if step.action in ("create", "update", "merge"):
            assert step.data is not None
            write_atomic(dest, step.data, file_mode(dest))
        elif step.action == "remove":
            remove(framework, step.path)
    written = set()
    for step in steps:
        if step.action == "conflict":
            assert step.data is not None
            sidecar = framework / f"{step.path}{CONFLICT}{target}"
            write_atomic(sidecar, step.data, file_mode(sidecar))
            written.add(sidecar)
    for sidecar in leftovers:
        if sidecar not in written:
            sidecar.unlink()
    if record_changes:
        write_atomic(record_path, new_record, file_mode(record_path))
    say(f"kit {target} into {framework}: {summary}")
    if review:
        say("review each conflict and kept file above; resolve, delete sidecars, rerun")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return install(args)
    except (CannotRun, OSError) as exc:
        # A crash is "could not run", never exit 1 ("conflicts to review").
        print(f"install: cannot run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
