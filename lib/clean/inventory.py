#!/usr/bin/env python3
"""Everything the bot left behind, for `clean` and `purge`. Read-only: nothing is removed here.

Sources, so nothing is missed when one of them is gone:
  * the data directory: cache/, worktrees/ and state/locks/, laid out as <owner>/<name>/pr-<N>;
  * every clone in workspace.json, plus every clone recorded in a cached meta.json (a repo
    removed from the workspace still has refs in its clone);
  * inside each clone: refs under refs/bugbot/ and worktree registrations pointing into the
    bot's worktrees/ directory, including ones whose directory was deleted behind git's back.

Output, NUL-separated records, each starting with its type:
  pr     owner name pr display clone cache worktree_dir worktree_admin refs lock   (11 fields)
  wt     clone admin_dir worktree_path exists                                      (5 fields)
  clone  clone display refs                                                        (4 fields)
lock is 0 (none), 1 (left by a process that no longer exists) or 2 (held right now).

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

PR_DIR = re.compile(r"^pr-([1-9][0-9]*)$")
# Ref components are escaped with "+" (lib/core/pr.sh), which never occurs in a GitHub name.
REF = re.compile(r"^refs/bugbot/([^/]+)/([^/]+)/pull/([1-9][0-9]*)/[^/]+$")
AREAS = {"cache": "cache", "worktree": "worktrees", "lock": os.path.join("state", "locks")}


def real(path):
    return os.path.realpath(path)


def inside(path, root):
    return real(path).startswith(real(root) + os.sep)


def git(clone, *args):
    result = subprocess.run(
        ["git", "-C", clone, "-c", "core.fsmonitor=false", *args],
        capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    return result.stdout if result.returncode == 0 else None


def lock_state(path):
    try:
        with open(os.path.join(path, "pid"), encoding="utf-8") as handle:
            pid = int(handle.read().strip())
        os.kill(pid, 0)
        return 2
    except (OSError, ValueError):
        return 1


def known_clones(data_dir, workspace):
    """{real clone path: display name} and {lowercase owner/name: display name}."""
    clones, names = {}, {}
    if os.path.exists(workspace):
        try:
            with open(workspace, encoding="utf-8") as handle:
                repos = json.load(handle).get("repos", {})
        except (OSError, ValueError):
            repos = {}
        for name, entry in repos.items():
            names[name.lower()] = name
            if isinstance(entry, dict) and entry.get("path"):
                clones.setdefault(real(entry["path"]), name)

    for meta_path in glob.glob(os.path.join(data_dir, "cache", "*", "*", "pr-*", "meta.json")):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            continue
        repo = meta.get("repo")
        clone = (meta.get("paths") or {}).get("clone")
        if repo:
            names.setdefault(repo.lower(), repo)
        if clone:
            clones.setdefault(real(clone), repo)

    return {path: name for path, name in clones.items() if os.path.isdir(os.path.join(path, ".git"))}, names


def main():
    parser = argparse.ArgumentParser(prog="inventory.py")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    data_dir = real(args.data_dir)
    worktrees_root = os.path.join(data_dir, "worktrees")
    clones, names = known_clones(data_dir, args.workspace)
    prs = {}

    def entry(owner, name, number):
        key = (owner.lower(), name.lower(), int(number))
        return prs.setdefault(key, {"clone": "", "cache": 0, "worktree_dir": 0,
                                    "worktree_admin": "", "refs": 0, "lock": 0})

    for kind, area in AREAS.items():
        for path in glob.glob(os.path.join(data_dir, area, "*", "*", "pr-*")):
            owner, name, leaf = path.split(os.sep)[-3:]
            match = PR_DIR.match(leaf)
            if not match or not os.path.isdir(path):
                continue
            item = entry(owner, name, match.group(1))
            if kind == "cache":
                item["cache"] = 1
            elif kind == "worktree":
                item["worktree_dir"] = 1
            else:
                item["lock"] = lock_state(path)

    records = []
    for clone, display in sorted(clones.items()):
        refs = (git(clone, "for-each-ref", "--format=%(refname)", "refs/bugbot/") or "").split()
        for ref in refs:
            match = REF.match(ref)
            if match:
                item = entry(match.group(1).replace("+", ""), match.group(2).replace("+", ""), match.group(3))
                item["refs"] += 1
                item["clone"] = item["clone"] or clone

        admin_root = os.path.join(clone, ".git", "worktrees")
        for admin in sorted(glob.glob(os.path.join(admin_root, "*"))):
            try:
                with open(os.path.join(admin, "gitdir"), encoding="utf-8") as handle:
                    worktree = os.path.dirname(handle.read().strip())
            except OSError:
                continue
            if not inside(worktree, worktrees_root):
                continue
            exists = os.path.isdir(worktree)
            records += ["wt", clone, admin, worktree, "1" if exists else "0"]
            parts = os.path.relpath(real(worktree), real(worktrees_root)).split(os.sep)
            match = PR_DIR.match(parts[-1]) if len(parts) == 3 else None
            if match:
                item = entry(parts[0], parts[1], match.group(1))
                item["worktree_admin"] = admin
                item["clone"] = item["clone"] or clone

        if refs:
            records += ["clone", clone, display or os.path.basename(clone), str(len(refs))]

    by_name = {}
    for clone, display in clones.items():
        if display:
            by_name.setdefault(display.lower(), clone)

    for (owner, name, number), item in sorted(prs.items()):
        key = f"{owner}/{name}"
        clone = item["clone"] or by_name.get(key, "")
        records += ["pr", owner, name, str(number), names.get(key, key), clone,
                    str(item["cache"]), str(item["worktree_dir"]), item["worktree_admin"],
                    str(item["refs"]), str(item["lock"])]

    sys.stdout.write("".join(field + "\0" for field in records))


if __name__ == "__main__":
    main()
