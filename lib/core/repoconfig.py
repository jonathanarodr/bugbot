#!/usr/bin/env python3
"""Per-repository review config: repos/<owner>/<name>.json.

The format is schemas/repo.schema.json. Every repository is described with the same keys; only
the values differ, so the bot holds no knowledge of any particular project. This module loads a
validated config and compiles its globs. Every group is optional and nothing is inferred: an
absent or empty group means "not configured", and `configured` lists the groups that have
entries so the triage card can say what its tier is based on.

Globs: `**` crosses directories, `*` and `?` stay within one segment, a trailing `/` matches
everything below. Matched against the path from the repository root.

Standard library only, compatible with the macOS system Python (3.9).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema  # noqa: E402


class ConfigError(Exception):
    pass


GROUPS = ("domains", "sensitive", "tests", "ignore")


def path_for(home, repo):
    owner, name = repo.lower().split("/", 1)
    return os.path.join(home, "repos", owner, f"{name}.json")


def compile_glob(pattern):
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


class Glob:
    def __init__(self, pattern):
        self.pattern = pattern
        self.regex = compile_glob(pattern)

    def match(self, path):
        return bool(self.regex.match(path))


def load(path):
    """Validated, compiled config. A missing file yields an empty one: every file is then medium
    risk, scoped by top-level directory, and only size can change the tier."""
    try:
        data = schema.read_document(path, "repo", required=False)
    except schema.ValidationError as error:
        raise ConfigError("\n".join(error.errors))
    data = data or {}
    return {
        "exists": bool(data),
        "path": path,
        "configured": [group for group in GROUPS if data.get(group)],
        "domains": [(d["name"], [Glob(g) for g in d["paths"]], d.get("risk", "medium"))
                    for d in data.get("domains", [])],
        "sensitive": [{"glob": Glob(s["glob"]), "reason": s["reason"],
                       "local_exec": s.get("local_exec", "allow")} for s in data.get("sensitive", [])],
        "tests": [Glob(g) for g in data.get("tests", [])],
        "ignore": [Glob(g) for g in data.get("ignore", [])],
        "approve_deny": [Glob(g) for g in data.get("approve_deny", [])],
        "large_diff": data.get("large_diff"),
        "hooks": data.get("hooks", {}),
    }
