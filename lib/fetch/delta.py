#!/usr/bin/env python3
"""Round delta: what changed in the pull request itself since the last round that was decided.

Comparing the commits pushed since that round does not work: a merge of the default branch brings
in everyone else's changes, and a rebase rewrites every sha. What matters is how the PR's own diff
changed, so the PR is compared with itself:

    before:  merge_base_1 -> head_1    (what the PR did at the decided round)
    now:     merge_base_2 -> head_2    (what the PR does now)

A file is in the delta when its patch differs between the two (hunk headers and context are
ignored, so a shift caused by the base moving does not count), or when it is in only one of them.
A sync with the base that changes nothing in the PR gives an empty delta.

`delta.patch` holds, for each file in the delta, the PR's current patch for it (merge_base_2 ->
head_2): the hunks a re-review has to read, in the same shape as diff.patch. A plain
`git diff head_1 head_2` would be wrong after a merge or rebase of the base: it shows the base's own
changes too. `changes` lists, per file, the patch lines that appeared and disappeared between the
two rounds, so a reviewer sees exactly what the author changed (a conflict resolution, a fix).

The previous round is the head the last bot review on GitHub covered (lib/fetch/rounds.py). fetch
fetches that commit by sha when the branch no longer has it (force-push) and pins it in
refs/bugbot/.../reviewed, so the comparison never depends on what the branch still contains.

  compute   write delta.patch and print the `delta` object of meta.json as JSON

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import json
import os
import re
import subprocess
import sys

HUNK = re.compile(rb"^@@ .* @@")


def git(clone, *args, check=True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    result = subprocess.run(["git", "-C", clone, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                             "-c", "diff.external=", *args], capture_output=True, env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout if result.returncode == 0 else None


def file_patches(clone, base, head):
    """{path: normalized patch body} of `git diff base head`, hunk headers dropped."""
    out = git(clone, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "-U0", base, head)
    patches, current, body = {}, None, []
    for line in out.split(b"\n"):
        if line.startswith(b"diff --git "):
            if current is not None:
                patches[current] = b"\n".join(body)
            current, body = None, []
            continue
        if line.startswith(b"+++ ") or line.startswith(b"--- "):
            name = line[4:]
            if name != b"/dev/null":
                current = name[2:].decode("utf-8", "replace")
            continue
        if line.startswith((b"+", b"-")) and current is not None:
            body.append(line)
        elif line.startswith(b"Binary files") and current is None:
            match = re.search(rb" b/(.+) differ$", line) or re.search(rb" a/(.+) and ", line)
            current = match.group(1).decode("utf-8", "replace") if match else None
            body.append(line)
    if current is not None:
        patches[current] = b"\n".join(body)
    return patches


def compute(args):
    resolved = {}
    for name in ("prev_head", "prev_base", "head", "base"):
        ref = getattr(args, name)
        sha = (git(args.clone, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False) or b"").decode().strip()
        if not sha:
            print(json.dumps({"from_head": None, "files": [], "lines": 0, "changes": [], "patch": None,
                              "unavailable": f"{name.replace('_', ' ')} {ref} not in the clone"}))
            return
        resolved[name] = sha
    before = file_patches(args.clone, resolved["prev_base"], resolved["prev_head"])
    now = file_patches(args.clone, resolved["base"], resolved["head"])
    files = sorted(path for path in set(before) | set(now) if before.get(path) != now.get(path))

    patch = b""
    if files:
        patch = git(args.clone, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames",
                    resolved["base"], resolved["head"], "--", *files)
    with open(args.out, "wb") as handle:
        handle.write(patch)
    changes = []
    for path in files:
        old = set((before.get(path) or b"").split(b"\n")) - {b""}
        new = set((now.get(path) or b"").split(b"\n")) - {b""}
        changes.append({"file": path, "added": len(new - old), "removed": len(old - new),
                        "state": "new" if path not in before else ("dropped" if path not in now else "changed")})
    print(json.dumps({"from_head": resolved["prev_head"], "files": files,
                      "lines": sum(c["added"] + c["removed"] for c in changes),
                      "changes": changes, "patch": args.out, "unavailable": None}))


def main():
    parser = argparse.ArgumentParser(prog="delta.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("compute")
    for name in ("clone", "prev-head", "prev-base", "head", "base", "out"):
        sub.add_argument(f"--{name}", required=True)
    sub.set_defaults(handler=compute)
    args = parser.parse_args()
    try:
        args.handler(args)
    except RuntimeError as error:
        print(f"delta: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
