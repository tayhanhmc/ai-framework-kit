#!/bin/bash
# kit_snapshot.sh: print a commit of the kit's current files/ and install.py, built through a
# temporary index, so install.py --revision can install work in progress. The kit's index,
# branch and working tree are untouched; the commit is unreferenced (like `git stash create`).
set -euo pipefail
kit="$(cd "$(dirname "$0")" && pwd)"
index="$(mktemp)"
trap '/bin/rm -f "$index"' EXIT
export GIT_INDEX_FILE="$index" GIT_AUTHOR_NAME=kit GIT_AUTHOR_EMAIL=kit@example.invalid
export GIT_COMMITTER_NAME=kit GIT_COMMITTER_EMAIL=kit@example.invalid
git -C "$kit" read-tree HEAD
git -C "$kit" add -A -- files install.py
git -C "$kit" commit-tree "$(git -C "$kit" write-tree)" -p HEAD -m "kit snapshot"
