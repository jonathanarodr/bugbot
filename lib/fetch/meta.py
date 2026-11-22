#!/usr/bin/env python3
"""Pull request metadata for fetch: read gh's JSON, validate it, write meta.json.

  pr     gh `pr view` JSON on stdin → NUL-separated fields for the shell (the PR text —
         title, body — is never handed to the shell, it stays in meta.json only).
  write  assemble meta.json from the PR JSON, the diff and the local refs, with the review
         knowledge of the base (knowledge.py); atomic.

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema  # noqa: E402
import knowledge  # noqa: E402  lib/fetch/knowledge.py

SHA = re.compile(r"^[0-9a-f]{40}$")
# A git ref name the shell can pass to `git fetch` without being read as an option.
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/@+-]+(?<!\.lock)(?<!/)(?<!\.)$")


def fail(message, code=1):
    print(f"fetch: {message}", file=sys.stderr)
    sys.exit(code)


def load_pr(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        fail(f"unreadable GitHub response: {error}")
    for field in ("number", "state", "isDraft", "headRefOid", "baseRefName", "baseRefOid"):
        if field not in data:
            fail(f"GitHub response without field {field}")
    if not SHA.match(data["headRefOid"]) or not SHA.match(data["baseRefOid"]):
        fail("GitHub response with an invalid sha")
    if not BRANCH.match(data["baseRefName"]):
        fail(f"unexpected base branch name: {data['baseRefName']!r}")
    return data


def pr(args):
    data = load_pr(args.json)
    head_owner = (data.get("headRepositoryOwner") or {}).get("login", "")
    fields = [
        str(data["number"]),
        data["state"],
        "true" if data["isDraft"] else "false",
        data["headRefOid"],
        data["baseRefName"],
        data["baseRefOid"],
        "true" if data.get("isCrossRepository") else "false",
        re.sub(r"[^A-Za-z0-9-]", "", head_owner),
        str(int(data.get("changedFiles") or 0)),
        str(int(data.get("additions") or 0)),
        str(int(data.get("deletions") or 0)),
        re.sub(r"[^A-Za-z0-9-]", "", (data.get("author") or {}).get("login", "")),
    ]
    sys.stdout.write("\0".join(fields) + "\0")


def files_from_diff(path):
    """Changed files with their status, from `git diff --name-status -z`."""
    with open(path, "rb") as handle:
        parts = handle.read().split(b"\0")
    files, index = [], 0
    while index < len(parts) and parts[index]:
        status = parts[index].decode()
        if status[0] in "RC":
            files.append({"status": status[0], "path": parts[index + 2].decode("utf-8", "replace"),
                          "previous_path": parts[index + 1].decode("utf-8", "replace")})
            index += 3
        else:
            files.append({"status": status[0], "path": parts[index + 1].decode("utf-8", "replace")})
            index += 2
    return files


def numstat(path):
    """{path: (added, deleted)}; binary files count as None."""
    stats = {}
    with open(path, "rb") as handle:
        parts = handle.read().split(b"\0")
    index = 0
    while index < len(parts) and parts[index]:
        added, deleted, name = parts[index].decode("utf-8", "replace").split("\t", 2)
        if name == "":
            name = parts[index + 2].decode("utf-8", "replace")
            index += 3
        else:
            index += 1
        binary = added == "-"
        stats[name] = (None if binary else int(added), None if binary else int(deleted))
    return stats


def write(args):
    data = load_pr(args.json)
    files = files_from_diff(args.name_status)
    stats = numstat(args.numstat)
    for entry in files:
        added, deleted = stats.get(entry["path"], (None, None))
        entry["additions"], entry["deletions"] = added, deleted
        entry["binary"] = added is None and entry["path"] in stats

    meta = {
        "$schema": schema.schema_ref("meta", args.dir),
        "version": 1,
        "repo": args.repo,
        "pr": data["number"],
        "url": data.get("url"),
        "title": data.get("title"),
        "body": data.get("body") or None,
        "author": (data.get("author") or {}).get("login"),
        "state": data["state"],
        "draft": data["isDraft"],
        "cross_repository": bool(data.get("isCrossRepository")),
        "head": {"sha": data["headRefOid"], "ref": data.get("headRefName"), "local_ref": args.head_ref},
        "base": {"branch": data["baseRefName"], "sha": data["baseRefOid"],
                 "local_ref": args.base_ref, "fetched_sha": args.base_sha},
        "merge_base": args.merge_base,
        "stats": {
            "files": len(files),
            "additions": sum(f["additions"] or 0 for f in files),
            "deletions": sum(f["deletions"] or 0 for f in files),
            "github": {"files": data.get("changedFiles"), "additions": data.get("additions"),
                       "deletions": data.get("deletions")},
        },
        "files": files,
        "knowledge": None,
        "rounds": None,
        "delta": None,
        "paths": {
            "clone": args.clone,
            "diff": os.path.join(args.dir, "diff.patch"),
            "files": os.path.join(args.dir, "files.txt"),
            "findings": os.path.join(args.dir, "findings.json"),
            "candidates": os.path.join(args.dir, "candidates"),
            "verdicts": os.path.join(args.dir, "verdicts"),
            "worktree": args.worktree or None,
        },
        "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    changed = sorted({f["path"] for f in files} | {f["previous_path"] for f in files if f.get("previous_path")})
    try:
        meta["knowledge"] = knowledge.discover(args.clone, args.base_ref, args.base_sha, changed)
    except RuntimeError as error:
        fail(f"reading review knowledge from the base: {error}")

    try:
        schema.validate(meta, "meta")
    except schema.ValidationError as error:
        fail("refusing to write an invalid meta.json:\n" + "\n".join(error.errors))

    with open(os.path.join(args.dir, "files.txt"), "w", encoding="utf-8") as handle:
        handle.writelines(entry["path"] + "\n" for entry in files)

    target = os.path.join(args.dir, "meta.json")
    fd, temp = tempfile.mkstemp(prefix=".meta.", suffix=".json", dir=args.dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, target)

    github = meta["stats"]["github"]
    note = ""
    if github["files"] is not None and github["files"] != meta["stats"]["files"]:
        note = f" · GitHub reports {github['files']} file(s)"
    print(f"{meta['stats']['files']}\t{meta['stats']['additions']}\t{meta['stats']['deletions']}\t{note}")


def set_delta(args):
    """Record the review rounds read on GitHub (lib/fetch/rounds.py) and, when there was a previous
    round, the delta (lib/fetch/delta.py) in the meta.json just written; atomic."""
    target = os.path.join(args.dir, "meta.json")
    with open(target, encoding="utf-8") as handle:
        meta = json.load(handle)
    meta["rounds"] = json.loads(args.rounds)
    meta["delta"] = json.loads(args.delta) if args.delta else None
    try:
        schema.validate(meta, "meta")
    except schema.ValidationError as error:
        fail("refusing to write an invalid meta.json:\n" + "\n".join(error.errors))
    fd, temp = tempfile.mkstemp(prefix=".meta.", suffix=".json", dir=args.dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, target)
    r, d = meta["rounds"], meta["delta"]
    if not r["last"]:
        print("no bot review yet: round 1")
    elif d is None or d["unavailable"]:
        print(f"{r['count']} bot round(s); delta unavailable" + (f": {d['unavailable']}" if d else ""))
    else:
        since = "no commit" if r["commits_since"] == 0 else f"{r['commits_since']} commit(s)"
        print(f"{r['count']} bot round(s), last at {r['last']['head'][:9]} · {since} since · "
              f"delta {len(d['files'])} file(s)")


def main():
    parser = argparse.ArgumentParser(prog="meta.py")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("pr")
    sub.add_argument("--json", required=True)
    sub.set_defaults(handler=pr)

    sub = commands.add_parser("set-delta")
    sub.add_argument("--dir", required=True)
    sub.add_argument("--delta", default="")
    sub.add_argument("--rounds", required=True)
    sub.set_defaults(handler=set_delta)

    sub = commands.add_parser("write")
    for name in ("json", "repo", "dir", "clone", "name-status", "numstat",
                 "head-ref", "base-ref", "base-sha", "merge-base"):
        sub.add_argument(f"--{name}", required=True)
    sub.add_argument("--worktree", default="")
    sub.set_defaults(handler=write)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
