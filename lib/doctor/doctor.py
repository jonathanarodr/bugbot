#!/usr/bin/env python3
"""bugbot doctor: read-only health check of the bot itself. Nothing is written or fetched.

Checks, in groups, each producing ok / warn / fail rows:
  format        bot JSON files follow the code style (`bugbot format` fixes them)
  environment   required tools and versions, BUGBOT_HOME, gh authentication and token overrides
  settings      bugbot.json and bugbot.local.json against schemas/settings.schema.json
  schemas       every schema loads and uses only keywords the validator supports
  workspace     workspace.json is valid, every clone exists and its remote still matches
  repo configs  repos/<owner>/<name>.json are valid, configured, cover workspace repos and match
                their clone (globs that match nothing, files in no domain)
  cache         meta.json and triage.json in .bugbot/cache are valid
  docs          every schema key and environment variable is documented in README.md

Output: one TSV row per check (group, status, label, detail) on stdout, read by the command.
Exit code: 0 when nothing failed, 1 otherwise. Warnings do not fail.

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))
sys.path.insert(0, os.path.join(HERE, "..", "setup"))
sys.path.insert(0, os.path.join(HERE, "..", "format"))
import format as codestyle  # noqa: E402  lib/format/format.py
import repoconfig  # noqa: E402
import schema  # noqa: E402

ROWS = []


def row(group, status, label, detail=""):
    ROWS.append((group, status, label, " ".join(str(detail).split())))


def run(*command, cwd=None):
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=cwd, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def version_tuple(text):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(part or 0) for part in match.groups()) if match else None


# ── environment ────────────────────────────────────────────────────────────────

def check_environment(offline, home, env_home):
    group = "environment"
    # Agents and skills find the CLI through $BUGBOT_HOME/bin/bugbot (then `command -v bugbot`).
    real = os.path.realpath(home)
    if not env_home:
        on_path = shutil.which("bugbot")
        if on_path and os.path.realpath(os.path.dirname(os.path.dirname(os.path.realpath(on_path)))) == real:
            row(group, "warn", "BUGBOT_HOME", f"not set; agents fall back to bugbot on PATH ({on_path}). "
                f"Add export BUGBOT_HOME={home} to ~/.zshrc")
        else:
            row(group, "warn", "BUGBOT_HOME", f"not set and bugbot is not on PATH: agents cannot find the CLI. "
                f"Add export BUGBOT_HOME={home} to ~/.zshrc")
    elif os.path.realpath(os.path.expanduser(env_home)) != real:
        row(group, "fail", "BUGBOT_HOME", f"{env_home} is not this bot ({home}): agents would run another copy")
    else:
        row(group, "ok", "BUGBOT_HOME", home)
    row(group, "ok", "python", f"{sys.version.split()[0]} ({sys.executable})") \
        if sys.version_info >= (3, 9) else row(group, "fail", "python", f"{sys.version.split()[0]} < 3.9")

    for tool, minimum, flag in (("git", (2, 30), "--version"), ("gh", (2, 0), "--version"),
                                ("zsh", (5, 8), "--version")):
        path = shutil.which(tool)
        if not path:
            row(group, "fail", tool, "not found in PATH")
            continue
        _, out, _ = run(tool, flag)
        found = version_tuple(out)
        if found and found[:2] < minimum:
            row(group, "warn", tool, f"{'.'.join(map(str, found))} is older than {'.'.join(map(str, minimum))}")
        else:
            row(group, "ok", tool, f"{'.'.join(map(str, found)) if found else '?'} ({path})")

    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            row(group, "warn", var, "set in the environment: it overrides the gh keyring login for every write")

    if not shutil.which("gh"):
        return
    if offline:
        row(group, "warn", "gh auth", "skipped (--offline)")
        return
    code, out, err = run("gh", "api", "user", "--jq", ".login")
    if code == 0 and out:
        row(group, "ok", "gh auth", f"logged in as {out}")
    else:
        row(group, "fail", "gh auth", (err or out or "not authenticated").splitlines()[0])


# ── settings ───────────────────────────────────────────────────────────────────

# ── format ────────────────────────────────────────────────────────────────────────────────

def check_format(home, workspace):
    """Read-only: `bugbot format` is what rewrites. Logic in lib/format/format.py."""
    group = "format"
    clean = 0
    for path in codestyle.targets(home, workspace):
        status, _wanted, detail = codestyle.inspect(path)
        label = os.path.relpath(path, home)
        if status == "ok":
            clean += 1
        elif status == "changed":
            row(group, "warn", label, "not in the code style (.editorconfig): run 'bugbot format'")
        elif status == "invalid":
            row(group, "warn", label, "not checked: invalid JSON (see the group that reads it)")
        else:
            row(group, "warn", label, f"not checked: {detail}")
    row(group, "ok", "code style", f"{clean} file(s) conforming")


def check_settings(home):
    group = "settings"
    files_ok = True
    import settings
    # init builds bugbot.json from the schema: every setting needs an x-init or a default, and
    # the result must pass the schema and the cross-field rules.
    try:
        settings.initial("acme")
        row(group, "ok", "init", "every setting has an initial value")
    except (settings.SettingsError, schema.SchemaError) as error:
        row(group, "fail", "init", str(error).splitlines()[0])
    for name, required in (("bugbot.json", True), ("bugbot.local.json", False)):
        path = os.path.join(home, name)
        if required and not os.path.exists(path):
            row(group, "fail", name, "not found: run 'bugbot init --org <name>'")
            files_ok = False
            continue
        try:
            data = schema.read_document(path, "settings", required=required)
        except schema.ValidationError as error:
            for message in error.errors[:5]:
                row(group, "fail", name, message)
            files_ok = False
            continue
        if data is None:
            row(group, "ok", name, "not present (optional)")
            continue
        row(group, "ok", name, "valid")
        if "$schema" not in data:
            row(group, "warn", name, 'no "$schema" key: editors will not validate it')

    # The values commands actually run with: files merged with the environment, then the rules
    # that span fields. Same code path as every other command (lib/core/settings.py).
    if not files_ok:
        return  # already reported per file; the merged view would repeat the same error
    try:
        resolved = settings.resolve_quietly(home)
    except settings.SettingsError as error:
        for message in str(error).splitlines()[:5]:
            row(group, "fail", "effective", message)
        return
    from_env = sorted({origin.split("(", 1)[1].rstrip(")") for _p, _e, _l, _v, origin in resolved
                       if origin.startswith("environment")})
    problems = settings.cross_check(resolved)
    for level, message in problems:
        row(group, level, "effective", message)
    if not any(level == "fail" for level, _ in problems):
        detail = "valid after merging files and environment"
        if from_env:
            detail += f" · from environment: {', '.join(from_env)}"
        row(group, "ok", "effective", detail)


# ── schemas ────────────────────────────────────────────────────────────────────

def check_schemas():
    group = "schemas"
    names = []
    for path in sorted(glob.glob(os.path.join(schema.SCHEMAS_DIR, "*.schema.json"))):
        name = os.path.basename(path)[:-len(".schema.json")]
        try:
            loaded = schema.load_schema(name)
        except (ValueError, schema.SchemaError, OSError) as error:
            row(group, "fail", name, str(error))
            continue
        names.append(name)
        if loaded.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            row(group, "warn", name, "does not declare Draft 2020-12")
        else:
            row(group, "ok", name, loaded.get("title", ""))
    return names


# ── workspace ──────────────────────────────────────────────────────────────────

def github_slug(url):
    match = re.match(r"^(?:https?://|ssh://)?(?:[^@/]+@)?[^:/@]*github[^:/]*(?::\d+)?[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


def github_default_branches(names):
    """{owner/name (lower): default branch} for the given repositories, in one GraphQL request.
    None when gh cannot answer (offline, auth, network): the caller then says it did not check."""
    if not names:
        return {}
    parts = []
    for index, name in enumerate(names):
        owner, repo = name.split("/", 1)
        parts.append(f"r{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) "
                     "{ nameWithOwner defaultBranchRef { name } }")
    code, out, _ = run("gh", "api", "graphql", "-f", "query=query { " + " ".join(parts) + " }")
    try:
        data = json.loads(out or "{}").get("data") or {}
    except ValueError:
        return None
    if code != 0 and not data:
        return None
    found = {}
    for index, name in enumerate(names):
        node = data.get(f"r{index}") or {}
        branch = (node.get("defaultBranchRef") or {}).get("name")
        if branch:
            found[name.lower()] = branch
    return found


def check_workspace(workspace, offline=False):
    group = "workspace"
    try:
        data = schema.read_document(workspace, "workspace", required=False)
    except schema.ValidationError as error:
        for message in error.errors[:5]:
            row(group, "fail", os.path.basename(workspace), message)
        return {}
    if data is None:
        row(group, "warn", os.path.basename(workspace), "not found: run 'bugbot setup'")
        return {}
    repos = data.get("repos", {})
    row(group, "ok", os.path.basename(workspace), f"valid · {len(repos)} repo(s)")
    if "$schema" not in data:
        row(group, "warn", os.path.basename(workspace), 'no "$schema" key: re-run bugbot setup to add it')

    enabled = [name for name, entry in repos.items() if entry.get("enabled") is not False]
    remote_defaults = None if offline else github_default_branches(enabled)
    if remote_defaults is None and enabled:
        row(group, "warn", "default branches", "not compared with GitHub" + (" (--offline)" if offline else ": gh did not answer"))

    for name, entry in sorted(repos.items(), key=lambda kv: kv[0].lower()):
        if entry.get("enabled") is False:
            row(group, "ok", name, "disabled")
            continue
        clone = entry.get("path", "")
        if not os.path.isdir(os.path.join(clone, ".git")):
            row(group, "fail", name, f"clone not found: {clone}")
            continue
        remote = entry.get("remote") or "origin"
        code, url, _ = run("git", "-C", clone, "remote", "get-url", remote)
        slug = github_slug(url) if code == 0 else None
        if slug is None:
            row(group, "fail", name, f"remote {remote} missing or not a GitHub URL")
        elif slug.lower() != name.lower():
            row(group, "fail", name, f"remote {remote} now points at {slug}")
        elif not entry.get("default_branch"):
            row(group, "warn", name, "no default branch: run 'bugbot setup --remote'")
        elif remote_defaults and remote_defaults.get(name.lower()) not in (None, entry["default_branch"]):
            row(group, "warn", name, f"default branch is {remote_defaults[name.lower()]} on GitHub, "
                                     f"workspace says {entry['default_branch']}: run 'bugbot setup'")
        else:
            row(group, "ok", name, f"{clone} · {entry['default_branch']}")
    return repos


# ── repo configs ───────────────────────────────────────────────────────────────

def check_repo_configs(home, repos, deep):
    group = "repo configs"
    import repo_config  # lib/setup/repo_config.py: tracked files and the config/clone comparison

    root = os.path.join(home, "repos")
    paths = sorted(glob.glob(os.path.join(root, "*", "*.json")))
    by_key = {os.path.relpath(p, root)[:-len(".json")]: p for p in paths}
    workspace_keys = {name.lower(): (name, entry) for name, entry in repos.items()
                      if entry.get("enabled") is not False}

    for stray in sorted(glob.glob(os.path.join(root, "*", "*.jsonc"))):
        row(group, "warn", os.path.relpath(stray, home), "not read: repo configs are .json")
    for key in sorted(set(workspace_keys) - set(by_key)):
        row(group, "warn", workspace_keys[key][0], "no config: run 'bugbot setup' to create the template")

    for key, path in sorted(by_key.items()):
        label = f"repos/{key}.json"
        try:
            data = schema.read_document(path, "repo")
        except schema.ValidationError as error:
            for message in error.errors[:5]:
                row(group, "fail", label, message)
            continue
        if data["repo"].lower() != key:
            row(group, "fail", label, f'"repo" is {data["repo"]}, file name says {key}')
            continue
        if key not in workspace_keys:
            row(group, "warn", label, "repository not in workspace.json (orphan config)")
            continue
        if "$schema" not in data:
            row(group, "warn", label, 'no "$schema" key: editors will not validate it')

        config = repoconfig.load(path)
        empty = [g for g in repoconfig.GROUPS if g not in config["configured"]]
        if empty:
            row(group, "warn", label, f"not configured: {', '.join(empty)}")

        problems = []
        name, entry = workspace_keys[key]
        clone = entry.get("path", "")
        if deep and config["configured"] and os.path.isdir(os.path.join(clone, ".git")):
            ref = repo_config.resolve_ref(clone, entry.get("default_branch") or "", entry.get("remote") or "origin")
            rows = repo_config.compare(config, repo_config.tracked(clone, ref))
            unmatched = [d for k, d in rows if k == "unmatched"]
            uncovered = [d for k, d in rows if k == "uncovered"]
            if unmatched:
                problems.append(f"{len(unmatched)} glob(s) match no file: {'; '.join(unmatched[:3])}")
            if uncovered:
                problems.append(f"{len(uncovered)} file(s) in no domain, e.g. {uncovered[0]}")
        for problem in problems:
            row(group, "warn", label, problem)
        if not empty and not problems:
            row(group, "ok", label, "valid · every group configured"
                + (" · matches clone" if deep else " · clone not checked (--quick)"))
        elif not deep and config["configured"]:
            row(group, "ok", label, "valid · clone not checked (--quick): globs that match no file go unseen")


# ── cache ──────────────────────────────────────────────────────────────────────

def check_cache(data_dir):
    group = "cache"
    checked = bad = 0
    for name in ("meta", "triage"):
        for path in sorted(glob.glob(os.path.join(data_dir, "cache", "*", "*", "pr-*", f"{name}.json"))):
            checked += 1
            try:
                schema.read_document(path, name)
            except schema.ValidationError as error:
                bad += 1
                where = os.path.relpath(path, data_dir)
                row(group, "fail", where, error.errors[0] + " (regenerate with fetch/triage, or clean the PR)")
    if checked and not bad:
        row(group, "ok", "cache", f"{checked} file(s) valid")
    elif not checked:
        row(group, "ok", "cache", "empty")
    # cache/, worktrees/ and state/locks/ hold only <owner>/<name>/pr-<N> directories. Anything
    # else (a file from an older layout, a flat worktree, a token cache) is unseen by clean.
    for area in ("cache", "worktrees", os.path.join("state", "locks")):
        stray = stray_entries(os.path.join(data_dir, area))
        if stray:
            where = os.path.relpath(stray[0], data_dir)
            row(group, "warn", area, f"{len(stray)} entry(ies) outside <owner>/<name>/pr-<N>, "
                f"e.g. {where}: unseen by 'bugbot clean' (remove them, or run 'bugbot purge')")


PR_DIR = re.compile(r"^pr-[1-9][0-9]*$")


def stray_entries(root):
    """Paths under root that are not an <owner>/<name>/pr-<N> directory or a parent of one.
    Finder's .DS_Store is not counted."""
    def children(path):
        return sorted(os.path.join(path, e) for e in os.listdir(path) if e != ".DS_Store")

    if not os.path.isdir(root):
        return []
    stray = []
    # clean removes the parents it empties, so an empty owner or name directory is left over too.
    for owner in children(root):
        if not os.path.isdir(owner) or os.path.islink(owner) or not children(owner):
            stray.append(owner)
            continue
        for name in children(owner):
            if not os.path.isdir(name) or os.path.islink(name) or not children(name):
                stray.append(name)
                continue
            for path in children(name):
                if not PR_DIR.match(os.path.basename(path)) or not os.path.isdir(path) or os.path.islink(path):
                    stray.append(path)
    return stray


# ── docs ───────────────────────────────────────────────────────────────────────

def schema_keys(node, root, out):
    if "$ref" in node:
        node = root["$defs"][node["$ref"].split("/")[-1]]
    for key, sub in node.get("properties", {}).items():
        if key != "$schema":
            out.add(key)
            schema_keys(sub, root, out)
    for key in ("items", "additionalProperties"):
        if isinstance(node.get(key), dict):
            schema_keys(node[key], root, out)
    for sub in node.get("oneOf", []) + node.get("anyOf", []):
        schema_keys(sub, root, out)


def check_docs(home, names):
    """Every schema has a <details> block in README.md's Schemas section, and every key and
    environment variable of a schema appears in that block."""
    group = "docs"
    readme_path = os.path.join(home, "README.md")
    try:
        readme = open(readme_path, encoding="utf-8").read()
    except OSError:
        row(group, "fail", "README.md", "not found")
        return
    if "## Schemas" not in readme:
        row(group, "fail", "README.md", "no '## Schemas' section")
        return
    section = readme[readme.index("## Schemas"):]
    following = re.search(r"\n## (?!Schemas)", section)
    section = section[:following.start()] if following else section
    blocks = {}
    for match in re.finditer(r"<details>\s*<summary><b>([^<]+)</b>(.*?)</details>", section, re.S):
        blocks[match.group(1).strip()] = match.group(2)

    for name in names:
        root = schema.load_schema(name)
        block = blocks.get(name)
        if block is None:
            row(group, "fail", name, "no <details> block in README.md Schemas section")
            continue
        keys = set()
        schema_keys(root, root, keys)
        envs = re.findall(r'"x-env":\s*"([A-Z0-9_]+)"', json.dumps(root))
        missing = sorted(k for k in keys if not re.search(r"(?<![\w$])" + re.escape(k) + r"(?!\w)", block))
        missing += sorted(e for e in envs if e not in block)
        if missing:
            row(group, "warn", name, f"{len(missing)} key(s) not documented: {', '.join(missing[:6])}")
        else:
            row(group, "ok", name, f"{len(keys)} key(s) documented")
    for extra in sorted(set(blocks) - set(names) - {"How to evolve a schema", "Who ran each output"}):
        row(group, "warn", extra, "documented in README.md but no such schema")

    check_docs_settings_example(group, blocks.get("settings"))
    check_docs_commands(group, home, readme)


def check_docs_settings_example(group, block):
    """The bugbot.json example in the settings block is what `bugbot init` writes (org aside)."""
    import settings
    if block is None:
        return
    match = re.search(r"\*\*Example\*\*[^\n]*bugbot\.json[^\n]*\n```json\n(.*?)\n```", block, re.S)
    if not match:
        row(group, "fail", "settings example", "no **Example** (`bugbot.json`) json block in the settings block")
        return
    try:
        example = json.loads(match.group(1))
        expected = settings.initial("acme")
    except ValueError as error:
        row(group, "fail", "settings example", f"invalid JSON: {error}")
        return
    except (settings.SettingsError, schema.SchemaError) as error:
        row(group, "fail", "settings example", str(error).splitlines()[0])
        return
    example.get("github", {}).pop("org", None)
    expected["github"].pop("org", None)
    diff = sorted(path for path, _leaf in settings.leaves(schema.load_schema("settings"))
                  if settings.lookup(example, path) != settings.lookup(expected, path))
    if diff:
        row(group, "fail", "settings example", f"differs from 'bugbot init --print' at {', '.join(diff[:6])}")
    else:
        row(group, "ok", "settings example", "matches 'bugbot init'")


def check_docs_commands(group, home, readme):
    """Every lib/commands/<name>.sh has a row in the README's ## Commands table, and no row
    names a command that does not exist."""
    if "## Commands" not in readme:
        row(group, "fail", "commands", "no '## Commands' section")
        return
    section = readme[readme.index("## Commands"):]
    following = re.search(r"\n## (?!Commands)", section)
    section = section[:following.start()] if following else section
    documented = set(re.findall(r"^\| `([a-z]+)[ `]", section, re.M))
    commands = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(home, "lib", "commands", "*.sh"))}
    missing, extra = sorted(commands - documented), sorted(documented - commands)
    if missing:
        row(group, "fail", "commands", f"not in the Commands table: {', '.join(missing)}")
    for name in extra:
        row(group, "warn", "commands", f"{name} in the Commands table but no lib/commands/{name}.sh")
    if not missing:
        row(group, "ok", "commands", f"{len(commands)} command(s) documented")


def main():
    parser = argparse.ArgumentParser(prog="doctor.py")
    parser.add_argument("--home", required=True)
    parser.add_argument("--env-home", default="")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--quick", action="store_true", help="skip comparing repo configs with clones")
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    only = {g.strip() for g in args.only.split(",") if g.strip()}

    def wanted(group):
        return not only or group in only

    names = []
    if wanted("format"):
        check_format(args.home, args.workspace)
    if wanted("environment"):
        check_environment(args.offline, args.home, args.env_home)
    if wanted("settings"):
        check_settings(args.home)
    if wanted("schemas") or wanted("docs"):
        names = check_schemas() if wanted("schemas") else [
            os.path.basename(p)[:-len(".schema.json")]
            for p in sorted(glob.glob(os.path.join(schema.SCHEMAS_DIR, "*.schema.json")))]
    repos = {}
    if wanted("workspace") or wanted("repos"):
        if wanted("workspace"):
            repos = check_workspace(args.workspace, offline=args.offline)
        else:
            try:
                repos = (schema.read_document(args.workspace, "workspace", required=False) or {}).get("repos", {})
            except schema.ValidationError:
                repos = {}
    if wanted("repos"):
        check_repo_configs(args.home, repos, deep=not args.quick)
    if wanted("cache"):
        check_cache(args.data_dir)
    if wanted("docs"):
        check_docs(args.home, names)

    for group, status, label, detail in ROWS:
        print("\t".join([group, status, label, detail]))
    sys.exit(1 if any(status == "fail" for _, status, _, _ in ROWS) else 0)


if __name__ == "__main__":
    main()
