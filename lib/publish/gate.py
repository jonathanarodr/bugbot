#!/usr/bin/env python3
"""Publication gate: nothing secret-shaped leaves the machine in a review.

The review text is written by agents that read the PR's worktree and the clone, which can hold
untracked secrets (.env, local.properties, keystores' passwords). Publishing is the last place a
leak can be stopped, so this check runs inside `bugbot publish`, never as the caller's choice.

Checked: the payload as posted (body and every comment), and its decoded JSON strings, since an
escape such as \\u0067hp_ hides a token from a regex over the raw text. Also checked: values of
KEY=VALUE files the clone keeps untracked (.env*, local.properties, secrets.properties) when they
appear verbatim.

  scan   exit 0 when clean; exit 3 with the first match's kind (never the secret itself)

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import json
import os
import re
import subprocess
import sys

PATTERNS = [
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("base64 private key", r"LS0tLS1CRUdJTi"),
    ("JWT", r"eyJ[0-9A-Za-z_\-]{8,}\.eyJ[0-9A-Za-z_\-]{8,}\."),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{30,}"),
    ("GitHub token", r"gh[pousr]_[0-9A-Za-z]{30,}"),
    ("GitHub fine-grained token", r"github_pat_[0-9A-Za-z_]{30,}"),
    ("Anthropic key", r"sk-ant-[0-9A-Za-z\-]{20,}"),
    ("OpenAI key", r"sk-(?:proj-)?[0-9A-Za-z]{32,}"),
    ("AWS access key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("Slack token", r"xox[abprs]-[0-9A-Za-z\-]{10,}"),
]
UNTRACKED_KV = re.compile(r"^(\.env(\..+)?|local\.properties|secrets\.properties)$")


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def untracked_values(clone):
    if not clone or not os.path.isdir(clone):
        return []
    out = subprocess.run(["git", "-C", clone, "ls-files", "--others", "--ignored", "--exclude-standard",
                          "--directory", "--no-empty-directory"], capture_output=True, text=True)
    values = []
    for name in out.stdout.splitlines():
        if "/" in name.rstrip("/") or not UNTRACKED_KV.match(name):
            continue
        try:
            text = open(os.path.join(clone, name), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if len(value) >= 12:
                    values.append((name, value))
    return values


def scan(args):
    raw = open(args.payload, encoding="utf-8", errors="replace").read()
    try:
        text = raw + "\n" + "\n".join(strings(json.loads(raw)))
    except ValueError:
        text = raw
    for kind, pattern in PATTERNS:
        if re.search(pattern, text):
            print(f"secret-shaped text: {kind}", file=sys.stderr)
            sys.exit(3)
    for name, value in untracked_values(args.clone):
        if value in text:
            print(f"a value from the clone's untracked {name}", file=sys.stderr)
            sys.exit(3)


def main():
    parser = argparse.ArgumentParser(prog="gate.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("scan")
    sub.add_argument("--payload", required=True)
    sub.add_argument("--clone", default="")
    sub.set_defaults(handler=scan)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
