#!/usr/bin/env python3
"""Review knowledge of a pull request: the instruction files the repository itself wrote.

Only three names are read, exact case: AGENTS.md, CLAUDE.md and REVIEW.md. For every changed file
(and the previous path of a rename) the candidates are the repository root and each parent
directory of that file, the same rule Claude Code Review uses. Other instruction formats
(.instructions.md, copilot-instructions.md) are left out on purpose: they are written to generate
code, not to review it, and a team that wants one in the review lists it in its REVIEW.md.

Files are read from the tip of the PR's base branch, never from the PR: a pull request must not
rewrite the rules it is judged by. A PR that changes one of these files gets it reviewed as code,
and is listed in `changed_by_pr` so the triage card can say so.

Nothing is copied: meta.json records the base sha and, for each file, its path and blob. Finders
and verifiers read them with `git -C <clone> show <ref>:<path>`, so all of them see the same bytes
whatever branch the clone or the worktree is on. Order: REVIEW.md before AGENTS.md/CLAUDE.md, the
most specific directory first. A symlink is resolved inside the base tree and listed once.

Standard library only, compatible with the macOS system Python (3.9).
"""

import os
import posixpath
import subprocess

NAMES = ("REVIEW.md", "AGENTS.md", "CLAUDE.md")
KIND = {"REVIEW.md": "review", "AGENTS.md": "project", "CLAUDE.md": "project"}


def git(clone, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    result = subprocess.run(["git", "-C", clone, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                             "-c", "diff.external=", "-c", "protocol.file.allow=never", *args],
                            capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def base_tree(clone, ref):
    """{path: (mode, blob)} of the instruction files at `ref`."""
    out = git(clone, "ls-tree", "-r", "-z", "--full-tree", ref)
    tree = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        path = path.decode("utf-8", "replace")
        mode, kind, blob = meta.decode().split()
        if kind == "blob":
            tree[path] = (mode, blob)
    return tree


def parents(path):
    """'' (the root) and every parent directory of `path`, root first."""
    parts = path.split("/")[:-1]
    return [""] + ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def resolve(clone, tree, path, hops=0):
    """(final path, blob) following symlinks inside the tree; None when it leaves the tree."""
    entry = tree.get(path)
    if entry is None or hops > 8:
        return None
    mode, blob = entry
    if mode != "120000":
        return path, blob
    target = git(clone, "cat-file", "blob", blob).decode("utf-8", "replace").strip()
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        return None
    return resolve(clone, tree, joined, hops + 1)


def discover(clone, ref, sha, changed):
    """Select and describe the knowledge files of one PR. `changed` holds the changed paths
    (current and previous of renames). Returns the `knowledge` object of meta.json."""
    tree = {p: v for p, v in base_tree(clone, ref).items() if posixpath.basename(p) in NAMES}
    covers = {}
    for path in changed:
        for directory in parents(path):
            for name in NAMES:
                candidate = posixpath.join(directory, name) if directory else name
                if candidate in tree:
                    covers.setdefault(candidate, set()).add(path)

    def priority(path):
        name = posixpath.basename(path)
        return (0 if name == "REVIEW.md" else 1, -path.count("/"), path)

    files, seen = [], {}
    for path in sorted(covers, key=priority):
        resolved = resolve(clone, tree, path)
        if resolved is None:
            continue
        target, blob = resolved
        if blob in seen:
            seen[blob]["also"].append(path)
            continue
        size = int(git(clone, "cat-file", "-s", blob).decode().strip())
        entry = {
            "path": target,
            "kind": KIND[posixpath.basename(path)],
            "covers": len(covers[path]),
            "bytes": size,
            "blob": blob,
            "symlink_from": path if target != path else None,
            "also": [],
        }
        seen[blob] = entry
        files.append(entry)

    return {
        "ref": sha,
        "bytes": sum(f["bytes"] for f in files),
        "files": files,
        "changed_by_pr": sorted({p for p in changed if posixpath.basename(p) in NAMES}),
    }
