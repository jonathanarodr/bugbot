#!/usr/bin/env python3
"""Code style of the bot's own JSON files, shared by `bugbot format` (writes) and `bugbot doctor`
(reports only).

Style (.editorconfig): UTF-8, LF, 2-space indent, one value per line, final newline. The
canonical text is json.dumps(indent=2) of the parsed data, so formatting never changes content.

  scan    one TSV row per file: status<TAB>path<TAB>detail
          status: ok | changed | invalid | unreadable
          --write rewrites the `changed` files (atomic, permissions kept)

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import schema  # noqa: E402

TARGETS = ("bugbot.json", "bugbot.local.json", "schemas/*.json", "repos/*/*.json")


def format_json(text):
    data = json.loads(text, object_pairs_hook=schema.no_duplicates("file"))
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def targets(home, workspace):
    """Files under the code style: workspace.json (wherever it lives) plus TARGETS in the home."""
    paths = [workspace] + [p for pattern in TARGETS for p in sorted(glob.glob(os.path.join(home, pattern)))]
    return [p for p in dict.fromkeys(paths) if os.path.isfile(p)]


def inspect(path):
    """(status, wanted_text_or_None, detail) for one file, without writing."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        return "unreadable", None, str(error)
    try:
        wanted = format_json(text)
    except (ValueError, schema.ValidationError):
        return "invalid", None, "invalid JSON (bugbot doctor shows where)"
    if wanted == text:
        return "ok", None, ""
    return "changed", wanted, "not in the code style (.editorconfig)"


def write(path, text):
    temporary = f"{path}.format-tmp"
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.chmod(temporary, os.stat(path).st_mode & 0o7777)
    os.replace(temporary, path)


def scan(args):
    for path in targets(args.home, args.workspace):
        status, wanted, detail = inspect(path)
        if status == "changed" and args.write:
            write(path, wanted)
            detail = "reformatted, content unchanged"
        print(f"{status}\t{path}\t{detail}")


def main():
    parser = argparse.ArgumentParser(prog="format.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("scan")
    sub.add_argument("--home", required=True)
    sub.add_argument("--workspace", required=True)
    sub.add_argument("--write", action="store_true")
    sub.set_defaults(handler=scan)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
