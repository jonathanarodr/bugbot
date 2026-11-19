#!/usr/bin/env python3
"""workspace.json: read, merge a setup selection into it, preview the change, write it.

Standard library only. The file is only ever replaced atomically (temp file + rename in
the same directory), so an interrupted setup leaves the previous version intact.

Merge rules, so re-running setup never loses hand-edited configuration:
  * fields setup discovers (path, clone_url, remote, default_branch) are refreshed;
  * every other field on an entry (review_md, enabled, anything added later) is kept;
  * a repository the scan did not find is kept untouched — it was not offered in the
    selector, so its absence from the selection is not a decision to drop it;
  * a repository that was offered and left unselected is removed.
"""

import argparse
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema  # noqa: E402

VERSION = 1
DISCOVERED = ("path", "clone_url", "remote", "default_branch")
DEFAULTS = {"review_md": None, "enabled": True}


def fail(message, code=1):
    print(f"workspace: {message}", file=sys.stderr)
    sys.exit(code)


def load(path):
    """Validated against schemas/workspace.schema.json; a missing file is an empty workspace."""
    try:
        data = schema.read_document(path, "workspace", required=False)
    except schema.ValidationError as error:
        fail("\n".join(error.errors) + "\nfix or move it before running setup", 2)
    return data or {"version": VERSION, "repos": {}}


def read_tsv(path, width):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line:
                fields = line.split("\t")
                rows.append(fields + [""] * (width - len(fields)))
    return rows


def build(args):
    current = load(args.current)
    candidates = {}
    for key, path, url, remote, default, _dirty, _fetched in read_tsv(args.candidates, 7):
        candidates[(key.lower(), path)] = (key, path, url, remote, default)

    offered = {key.lower() for key, _ in candidates}
    selected = {}
    for key, path in read_tsv(args.selection, 2):
        match = candidates.get((key.lower(), path))
        if match is None:
            fail(f"selection {key} at {path} is not among the scanned candidates")
        selected[match[0].lower()] = match

    existing = {name.lower(): (name, entry) for name, entry in current["repos"].items()}
    repos = {}

    for lowered, (name, entry) in existing.items():
        if lowered not in offered:
            repos[name] = entry

    for lowered, (key, path, url, remote, default) in selected.items():
        name, entry = existing.get(lowered, (key, {}))
        entry = dict(DEFAULTS, **entry)
        entry.update(path=path, clone_url=url, remote=remote)
        entry["default_branch"] = default or entry.get("default_branch")
        repos[name] = entry

    document = {
        "$schema": schema.schema_ref("workspace", os.path.dirname(os.path.abspath(args.current))),
        "version": VERSION,
        "github_user": args.gh_user or current.get("github_user"),
        "org": args.org,
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repos": {name: order(repos[name]) for name in sorted(repos, key=str.lower)},
    }
    json.dump(document, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def order(entry):
    known = [field for field in DISCOVERED + tuple(DEFAULTS) if field in entry]
    return {field: entry[field] for field in known + sorted(set(entry) - set(known))}


def diff(args):
    """One line per repository, ready for the confirmation screen: + added, - removed,
    ~ changed (with the fields), = unchanged."""
    before = load(args.current)["repos"]
    after = load(args.proposed)["repos"]
    lowered_before = {name.lower(): name for name in before}

    for name in after:
        old_name = lowered_before.pop(name.lower(), None)
        if old_name is None:
            print(f"+\t{name}\t{after[name].get('path')}")
            continue
        changed = [
            f"{field}: {before[old_name].get(field)} → {after[name].get(field)}"
            for field in DISCOVERED
            if before[old_name].get(field) != after[name].get(field)
        ]
        print(f"{'~' if changed else '='}\t{name}\t{'; '.join(changed) or after[name].get('path')}")

    for old_name in lowered_before.values():
        print(f"-\t{old_name}\t{before[old_name].get('path')}")


def write(args):
    with open(args.proposed, encoding="utf-8") as handle:
        content = handle.read()
    try:
        schema.validate(json.loads(content), "workspace")
    except schema.ValidationError as error:
        fail("refusing to write an invalid workspace:\n" + "\n".join(error.errors), 2)

    target = os.path.abspath(args.current)
    fd, temp = tempfile.mkstemp(prefix=".workspace.", suffix=".json", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def current(args):
    """key<TAB>path of every configured repository, to preselect them in the selector."""
    for name, entry in load(args.current)["repos"].items():
        print(f"{name}\t{entry.get('path', '')}")


def listing(args):
    """key<TAB>path<TAB>default_branch<TAB>remote of every enabled repository."""
    for name, entry in load(args.current)["repos"].items():
        if entry.get("enabled") is False:
            continue
        print("\t".join([name, entry.get("path") or "", entry.get("default_branch") or "",
                         entry.get("remote") or "origin"]))


def lookup(args):
    """One repository as key, path, remote, default_branch (NUL-separated). The key is matched
    case-insensitively and returned as recorded. Exit 3 when it is not configured."""
    repos = load(args.current)["repos"]
    wanted = args.repo.lower()
    for name, entry in repos.items():
        if name.lower() != wanted:
            continue
        if entry.get("enabled") is False:
            fail(f"{name} is disabled in workspace.json (enabled: false)", 3)
        fields = [name, entry.get("path") or "", entry.get("remote") or "origin",
                  entry.get("default_branch") or ""]
        sys.stdout.write("\0".join(fields) + "\0")
        return
    sys.exit(3)


def main():
    parser = argparse.ArgumentParser(prog="workspace.py")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("build")
    sub.add_argument("--current", required=True)
    sub.add_argument("--candidates", required=True)
    sub.add_argument("--selection", required=True)
    sub.add_argument("--gh-user", default="")
    sub.add_argument("--org", required=True)
    sub.set_defaults(handler=build)

    sub = commands.add_parser("diff")
    sub.add_argument("--current", required=True)
    sub.add_argument("--proposed", required=True)
    sub.set_defaults(handler=diff)

    sub = commands.add_parser("write")
    sub.add_argument("--current", required=True)
    sub.add_argument("--proposed", required=True)
    sub.set_defaults(handler=write)

    sub = commands.add_parser("current")
    sub.add_argument("--current", required=True)
    sub.set_defaults(handler=current)

    sub = commands.add_parser("list")
    sub.add_argument("--current", required=True)
    sub.set_defaults(handler=listing)

    sub = commands.add_parser("lookup")
    sub.add_argument("--current", required=True)
    sub.add_argument("--repo", required=True)
    sub.set_defaults(handler=lookup)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
