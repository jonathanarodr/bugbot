#!/usr/bin/env python3
"""Bot settings: bugbot.json (written by `bugbot init`) merged with bugbot.local.json (personal) and the environment.

The format is defined by schemas/settings.schema.json, the single source of truth: each leaf
setting there carries its type, limits, description and environment variable (x-env). This
module only walks that schema; adding a setting means adding it to the schema and giving it a
value in bugbot.json, or a `default` in the schema when leaving it out has a meaning (0: no limit).

Precedence, highest first: environment > bugbot.local.json > bugbot.json > the schema's default. Command-line flags
are applied afterwards by each command.

Standard library only, compatible with the macOS system Python (3.9).

  load      NUL-separated records for the shell: name, origin, kind, count, values...
  describe  one TSV row per setting for `bugbot config`
  init      write bugbot.json from the schema's x-init values, for `bugbot init`
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema  # noqa: E402

SHARED = "bugbot.json"
LOCAL = "bugbot.local.json"


class SettingsError(Exception):
    """Invalid settings. Commands print it and exit 2; `bugbot doctor` reports it instead."""


def fail(message):
    raise SettingsError(message)


def leaves(node, prefix=""):
    """(dotted path, leaf schema) for every setting declared in the settings schema."""
    for key, sub in node.get("properties", {}).items():
        if key == "$schema":
            continue
        path = f"{prefix}{key}"
        if sub.get("type") == "object":
            yield from leaves(sub, path + ".")
        else:
            yield path, sub


def lookup(data, path):
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def from_env(leaf, name, raw):
    """Environment values are strings: an integer is parsed, a list is a JSON array or a single
    item — never split on commas, which is how paths with spaces get mangled."""
    if leaf.get("type") == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    if leaf.get("type") == "boolean":
        return {"true": True, "1": True, "false": False, "0": False}.get(raw.strip().lower(), raw)
    if leaf.get("type") == "array":
        if raw.lstrip().startswith("["):
            try:
                return json.loads(raw)
            except ValueError as error:
                fail(f"{name}: invalid JSON array in the environment: {error}")
        return [raw]
    return raw


def cross_check(resolved):
    """Rules the schema cannot express because they span fields or depend on how values are
    compared at run time. Returns (level, message) pairs; level is "fail" or "warn".
    Shared by every command (fails stop them) and by `bugbot doctor` (reports all)."""
    values = {path: value for path, _env, _leaf, value, _origin in resolved}
    problems = []

    skip = values.get("hooks.labels.skip", [])
    trigger = values.get("hooks.labels.trigger", [])
    for name, labels in (("hooks.labels.skip", skip), ("hooks.labels.trigger", trigger)):
        seen = {}
        for label in labels:
            key = label.lower()
            if key in seen and seen[key] != label:
                problems.append(("warn", f"{name}: {seen[key]!r} and {label!r} are the same label "
                                         "(labels are compared case-insensitively)"))
            seen.setdefault(key, label)
    both = sorted({l.lower() for l in skip} & {l.lower() for l in trigger})
    if both:
        problems.append(("fail", f"hooks.labels: {', '.join(both)} is in both skip and trigger; "
                                 "skip is checked first, so such a PR is never reviewed"))

    parallel, max_prs = values.get("review.parallel"), values.get("review.max_prs")
    if parallel is not None and max_prs is not None and int(parallel) > int(max_prs):
        problems.append(("fail", f"review.parallel ({parallel}) is above review.max_prs ({max_prs}): a run never "
                                 f"holds more than {max_prs} PRs, so the extra slots never run; lower parallel "
                                 "or raise max_prs"))
    delta_from, max_rounds = values.get("review.delta_from_round"), values.get("review.max_rounds")
    if delta_from is not None and max_rounds is not None and int(delta_from) > int(max_rounds):
        problems.append(("warn", f"review.delta_from_round ({delta_from}) is above review.max_rounds ({max_rounds}): "
                                 "the bot stops before any delta review"))
    return problems


def resolve_quietly(home):
    """Effective settings, raising SettingsError instead of exiting (used by doctor)."""
    return resolve(home)


def resolve(home):
    if not os.path.exists(os.path.join(home, SHARED)):
        fail(f"{SHARED} not found: run 'bugbot init' to create it with the default values")
    try:
        shared = schema.read_document(os.path.join(home, SHARED), "settings")
        local = schema.read_document(os.path.join(home, LOCAL), "settings", required=False) or {}
    except schema.ValidationError as error:
        fail("\n".join(error.errors))

    root = schema.load_schema("settings")
    resolved = []
    for path, leaf in leaves(root):
        env = leaf.get("x-env")
        legacy_env = "BUGBOT_REVIEW_MAX_NITS" if path == "review.max_lows" else None
        source_env = env if env and env in os.environ else (
            legacy_env if legacy_env and legacy_env in os.environ else None)
        if source_env:
            value, origin = from_env(leaf, source_env, os.environ[source_env]), f"environment ({source_env})"
            errors = []
            schema._validate(value, leaf, root, [path], errors)
            if errors:
                fail(f"{source_env}: {errors[0].split(': ', 1)[-1]}")
        else:
            value, found = lookup(local, path)
            origin = LOCAL
            if not found:
                value, found = lookup(shared, path)
                origin = SHARED
            if not found and "default" in leaf:
                value, found, origin = leaf["default"], True, "default"
            if not found:
                fail(f"{path} is not set in {SHARED}")
        if leaf.get("type") == "array":
            value = [item.strip() for item in value]
            if path == "scan.roots":
                value = [os.path.expanduser(item) for item in value]
        resolved.append((path, env, leaf, value, origin))
    errors = [message for level, message in cross_check(resolved) if level == "fail"]
    if errors:
        fail("\n".join(errors))
    return resolved


def load(args):
    fields = []
    for _path, env, _leaf, value, origin in resolve(args.home):
        if isinstance(value, bool):
            value = "true" if value else "false"
        items = value if isinstance(value, list) else [str(value)]
        kind = "list" if isinstance(value, list) else "scalar"
        fields += [env, origin, kind, str(len(items))] + items
    sys.stdout.write("\0".join(fields) + "\0")


def describe(args):
    """TSV rows from a previous `load` output (stdin), so what is described is exactly what the
    shell loaded — the shell may no longer export the original environment."""
    root = schema.load_schema("settings")
    by_env = {leaf.get("x-env"): (path, leaf) for path, leaf in leaves(root)}
    fields = sys.stdin.read().split("\0")
    index = 0
    while index + 3 < len(fields):
        env, origin, kind, count = fields[index:index + 4]
        values = fields[index + 4:index + 4 + int(count)]
        index += 4 + int(count)
        path, leaf = by_env.get(env, (env, {}))
        shown = json.dumps(values, ensure_ascii=False) if kind == "list" else values[0]
        print("\t".join([env, path, shown, origin, leaf.get("description", "")]))


def as_json(args):
    """One JSON object from a previous `load` output (stdin): the settings in use, shaped like
    bugbot.json, plus where each value came from. For agents and scripts."""
    root = schema.load_schema("settings")
    by_env = {leaf.get("x-env"): (path, leaf) for path, leaf in leaves(root)}
    fields = sys.stdin.read().split("\0")
    values, origins, index = {}, {}, 0
    while index + 3 < len(fields):
        env, origin, kind, count = fields[index:index + 4]
        items = fields[index + 4:index + 4 + int(count)]
        index += 4 + int(count)
        path, leaf = by_env.get(env, (env, {}))
        types = leaf.get("type")
        if kind == "list":
            value = items
        elif types == "integer":
            value = int(items[0])
        elif types == "boolean":
            value = items[0] == "true"
        else:
            value = items[0]
        node = values
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        origins[path] = origin
    json.dump({"settings": values, "origins": origins, "paths": {
        key: os.environ.get(key) for key in ("BUGBOT_HOME", "BUGBOT_WORKSPACE_FILE", "BUGBOT_DATA_DIR", "BUGBOT_RETRO_DIR")}},
        sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def initial(org):
    """bugbot.json built from the schema: every setting at its `x-init` value (the reference
    configuration), github.org from the caller when given (left out otherwise: any owner). A setting with neither `x-init` nor `default`
    is refused, so a new key cannot be added to the schema without an initial value."""
    root = schema.load_schema("settings")
    data = {"$schema": "schemas/settings.schema.json"}
    missing = []
    for path, leaf in leaves(root):
        if path == "github.org":
            if not org:
                continue  # no org: setup offers clones of every owner (the schema's default)
            value = org
        elif "x-init" in leaf:
            value = leaf["x-init"]
        elif "default" in leaf:
            value = leaf["default"]
        else:
            missing.append(path)
            continue
        node = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    if missing:
        fail(f"settings schema: no x-init or default for {', '.join(missing)}")
    errors = []
    schema._validate(data, root, root, [], errors)
    if errors:
        fail("\n".join(errors))
    resolved = [(path, leaf.get("x-env"), leaf, lookup(data, path)[0], SHARED) for path, leaf in leaves(root)]
    errors = [message for level, message in cross_check(resolved) if level == "fail"]
    if errors:
        fail("\n".join(errors))
    return data


def init(args):
    """Write bugbot.json with the initial values (atomic). Refuses to overwrite unless --force;
    --print writes it to stdout instead. Prints the path written."""
    data = initial(args.org)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.print:
        sys.stdout.write(text)
        return
    path = os.path.join(args.home, SHARED)
    if os.path.exists(path) and not args.force:
        fail(f"{SHARED} already exists: use --force to replace it with the default values")
    handle, tmp = tempfile.mkstemp(dir=args.home, prefix=".bugbot.json.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as error:
        if os.path.exists(tmp):
            os.unlink(tmp)
        fail(f"cannot write {path}: {error.strerror}")
    print(path)


def main():
    try:
        _main()
    except SettingsError as error:
        print(f"bugbot: {error}", file=sys.stderr)
        sys.exit(2)


def _main():
    parser = argparse.ArgumentParser(prog="settings.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("load", load), ("describe", describe), ("json", as_json)):
        sub = commands.add_parser(name)
        sub.add_argument("--home", required=True)
        sub.set_defaults(handler=handler)
    sub = commands.add_parser("init")
    sub.add_argument("--home", required=True)
    sub.add_argument("--org", default="")
    sub.add_argument("--force", action="store_true")
    sub.add_argument("--print", action="store_true")
    sub.set_defaults(handler=init)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
