#!/usr/bin/env python3
"""Per-repository review config: the minimal file setup creates, and its check against a clone.

Nothing is inferred from the repository. A guessed rule looks exactly like a decided one, and a
wrong guess (a "sensitive" file that is not, a "low risk" path that is) silently changes the
tier of every PR. So `template` writes only what identifies the file ($schema, $metadata, repo);
every review group is optional and added by hand when someone decides it (formats and meaning in
schemas/repo.schema.json, reference globs in README.md).

  template  print the minimal config for one repository
  check     compare a config with the clone: globs matching no file, files in no domain

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import repoconfig  # noqa: E402
import schema  # noqa: E402

def git(clone, *args):
    result = subprocess.run(["git", "-C", clone, "-c", "core.hooksPath=/dev/null",
                             "-c", "core.fsmonitor=false", *args], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def tracked(clone, ref):
    """Paths tracked at `ref`, exactly as stored. -z is required: without it git quotes any path
    with special characters ("Modules/Câmera…" comes out wrapped in quotes and escaped), and a
    quoted path would turn into a glob that matches nothing."""
    out = git(clone, "ls-tree", "-r", "-z", "--name-only", "--full-tree", ref) or b""
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def resolve_ref(clone, default_branch, remote):
    for ref in (f"refs/remotes/{remote}/{default_branch}", default_branch, "HEAD"):
        if ref and git(clone, "rev-parse", "--verify", "--quiet", ref + "^{commit}"):
            return ref
    return "HEAD"


def template(args):
    data = {
        "$schema": schema.schema_ref("repo", args.out_dir) if args.out_dir else "../../schemas/repo.schema.json",
        "$metadata": {
            "schema_version": 1,
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "repo": args.repo,
    }
    schema.validate(data, "repo")
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def check(args):
    """Rows: kind<TAB>detail. kinds: unmatched (glob matching no tracked file), uncovered (file
    in no domain; only when domains are configured)."""
    try:
        config = repoconfig.load(args.config)
    except repoconfig.ConfigError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
    files = tracked(args.clone, resolve_ref(args.clone, args.default_branch, args.remote))
    for kind, detail in compare(config, files):
        print(f"{kind}\t{detail}")


def compare(config, files):
    """(kind, detail) pairs for a loaded config against the tracked files of its repository."""
    rows = []
    globs = [(f"domain {name}", g) for name, gs, _risk in config["domains"] for g in gs]
    globs += [("sensitive", s["glob"]) for s in config["sensitive"]]
    globs += [("tests", g) for g in config["tests"]] + [("ignore", g) for g in config["ignore"]]
    globs += [("approve_deny", g) for g in config["approve_deny"]]
    for where, glob in globs:
        if not any(glob.match(p) for p in files):
            rows.append(("unmatched", f"{where}: {glob.pattern}"))
    if config["domains"]:
        for path in files:
            if not any(g.match(path) for _n, gs, _r in config["domains"] for g in gs):
                rows.append(("uncovered", path))
    return rows


def main():
    parser = argparse.ArgumentParser(prog="repo_config.py")
    commands = parser.add_subparsers(dest="command", required=True)


    sub = commands.add_parser("template")
    sub.add_argument("--repo", required=True)
    sub.add_argument("--out-dir", default="")
    sub.set_defaults(handler=template)

    sub = commands.add_parser("check")
    sub.add_argument("--clone", required=True)
    sub.add_argument("--config", required=True)
    sub.add_argument("--default-branch", default="")
    sub.add_argument("--remote", default="origin")
    sub.set_defaults(handler=check)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
