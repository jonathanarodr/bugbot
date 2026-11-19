#!/usr/bin/env python3
"""Triage: decide tier, depth and scope of a PR from facts only, before any model runs.

Inputs: the PR's meta.json (written by fetch), the repository's config (repos/<owner>/<name>.json),
the CI rollup from GitHub (JSON on stdin of the `ci` step) and the previous triage, if any.
Never reads the PR's title, body or comments: nothing here can be steered by text in the PR.

Tier rules (a file's risk comes from the first domain that matches it; medium without domains):
  base         highest risk among the reviewable files' domains (all low → low)
  sensitive    any changed file matches `sensitive`         → high, floor deep
  large        production lines above large_diff            → one level up
  broad        production domains ≥ broad_change (0: off)   → one level up
"Production" is a file outside low-risk domains, tests and ignore: tests and docs never make a PR
large or broad. A test file takes no part in the base risk nor in `sensitive` (a tests-only PR is
low). Depth suggestion: low → quick, medium → standard, high → deep. Only `sensitive`
sets a floor: a high tier from domains, size or breadth suggests deep and nothing more.

Hooks (settings `hooks`, evaluated before the card, in this order; the first that applies decides):
  draft: false            the PR is a draft                         → skip
  stale_days              > 0 and the head commit is that old       → skip
  exclude_teams           every request that reaches you comes from → skip
                          one of these teams (a request by name or
                          through another of your teams keeps it)
  labels.skip             the PR has one of these labels            → skip
  labels.trigger          not empty and the PR has none of them     → skip
  reviewers.max           > 0 and that many people already reviewed → skip
  ci: passed              a required check failed or was cancelled  → skip
                          a required check is still running         → wait
  otherwise                                                         → review
Labels, draft and required checks are read live from GitHub on every triage (state.json from the
`state` step), because they change without a new commit and meta.json is from the last fetch.
Hooks apply to every tier: the configuration is explicit, and skipping a high-tier PR with a label
or the reviewer limit is a risk the configuration chose, not something the code silently undoes.

Output: triage.json next to meta.json, written atomically. The record of what the reviewer
then chose is appended by the command (`decision`), so the retro can compare suggestion and
choice later.

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
import harness  # noqa: E402
import repoconfig  # noqa: E402
import schema  # noqa: E402

TIERS = ("low", "medium", "high")
DEPTHS = ("quick", "standard", "deep")
DEPTH_FOR = {"low": "quick", "medium": "standard", "high": "deep"}
# Check buckets as `gh pr checks --json bucket` reports them (cli/cli pkg/cmd/pr/checks/aggregate.go):
#   pass      SUCCESS
#   fail      ERROR, FAILURE, TIMED_OUT, ACTION_REQUIRED
#   cancel    CANCELLED
#   skipping  SKIPPED, NEUTRAL
#   pending   anything else (QUEUED, IN_PROGRESS, WAITING, REQUESTED, PENDING, EXPECTED, STALE)
# gh also keeps only the latest run of a check, so a re-run that passed hides the old failure.
# The bot keeps gh's vocabulary instead of translating it.
BUCKETS = ("pass", "fail", "cancel", "skipping", "pending")


def fail(message):
    print(f"triage: {message}", file=sys.stderr)
    sys.exit(2)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_atomic(path, data):
    data = {"$schema": schema.schema_ref("triage", os.path.dirname(path)), **{k: v for k, v in data.items() if k != "$schema"}}
    try:
        schema.validate(data, "triage")
    except schema.ValidationError as error:
        fail("refusing to write an invalid triage.json:\n" + "\n".join(error.errors))
    fd, temp = tempfile.mkstemp(prefix=".triage.", suffix=".json", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, path)


def checks_summary(checks):
    """Counts per gh bucket, the failing names and an overall state for a list of checks."""
    counts = {bucket: 0 for bucket in BUCKETS}
    failing = []
    for item in checks or []:
        bucket = item.get("bucket") if item.get("bucket") in counts else "pending"
        counts[bucket] += 1
        if bucket in ("fail", "cancel"):
            failing.append(item.get("name") or "?")
    if counts["fail"] or counts["cancel"]:
        state = "fail"
    elif counts["pending"]:
        state = "pending"
    elif counts["pass"]:
        state = "pass"
    else:
        state = "none"
    return dict(counts, total=len(checks or []), state=state, failing=sorted(set(failing))[:10])


def count_reviewers(reviews, author, me, ignore):
    """People who already reviewed, counted like GitHub's reviewDecision: for each distinct person
    the latest *decisive* review — APPROVED, CHANGES_REQUESTED or DISMISSED — wins; a later
    COMMENTED does not undo an approval, a DISMISSED does. Counted when that review is APPROVED or
    CHANGES_REQUESTED. Not counted: bots (REST user.type Bot), logins in `ignore`, the PR author,
    the reviewer running the bot (a re-review must not block itself), comment-only reviewers and
    pending drafts. Reviews arrive oldest first from the REST API."""
    skip = {login.lower() for login in ignore} | {(author or "").lower(), (me or "").lower()}
    latest = {}
    for review in reviews or []:
        user = review.get("user") or {}
        login = user.get("login") or ""
        if not login or user.get("type") == "Bot" or login.lower() in skip:
            continue
        state = review.get("state")
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login.lower()] = (login, state)
    return sorted((login for login, state in latest.values() if state != "DISMISSED"), key=str.lower)


def idle_days(stamp, now=None):
    """Whole days since an ISO 8601 timestamp (GitHub's committedDate); None when unknown."""
    if not stamp:
        return None
    when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0, (now - when).days)


def requested_of_me(requests, me, my_teams):
    """Review requests that reach the reviewer: their own login, or a team they belong to.
    gh reports a team as org/slug and a user by login; both compared case-insensitively."""
    me = (me or "").lower()
    teams = {t.lower() for t in my_teams or []}
    mine = []
    for request in requests or []:
        if request.get("__typename") == "Team":
            slug = request.get("slug") or ""
            if slug.lower() in teams:
                mine.append(slug)
        elif me and (request.get("login") or "").lower() == me:
            mine.append(request["login"])
    return mine


def evaluate_hooks(hooks, live):
    """review / skip / wait, with the rule that decided and why."""
    lowered = {label.lower() for label in live["labels"]}
    skip_labels = [l for l in hooks["labels"]["skip"] if l.lower() in lowered]
    trigger = hooks["labels"]["trigger"]
    limit = hooks["reviewers"]["max"]
    reviewers = live["reviewers"]
    required = live["ci"]["required"]

    if live["draft"] and not hooks["draft"]:
        return "skip", "draft", "PR is a draft (hooks.draft is false)"
    idle = live.get("idle_days")
    if hooks["stale_days"] and idle is not None and idle >= hooks["stale_days"]:
        return "skip", "stale_days", f"no commit for {idle} days (stale after {hooks['stale_days']})"
    excluded = {t.lower() for t in hooks["exclude_teams"]}
    mine = live["requested"]
    if excluded and mine and all(r.lower() in excluded for r in mine):
        return "skip", "exclude_teams", f"requested only via excluded team(s): {', '.join(mine)}"
    if skip_labels:
        return "skip", "labels.skip", f"label {skip_labels[0]}"
    if trigger and not any(l.lower() in lowered for l in trigger):
        return "skip", "labels.trigger", f"none of the trigger labels: {', '.join(trigger)}"
    if limit and len(reviewers) >= limit:
        return "skip", "reviewers.max", f"already reviewed by {len(reviewers)} of max {limit}: {', '.join(reviewers)}"
    if hooks["ci"] == "passed":
        if not live["ci"]["read"]:
            return "wait", "ci", f"cannot read checks: {live['ci']['error']}"
        if not required["available"]:
            return "review", None, "no required checks reported: CI not gating"
        if required["state"] == "fail":
            return "skip", "ci", f"required check failed: {', '.join(required['failing'][:3])}"
        if required["state"] == "pending":
            return "wait", "ci", f"{required['pending']} required check(s) still running"
    return "review", None, None


def domain_of(path, config):
    """(name, risk) of the first domain that matches the path."""
    for name, patterns, risk in config["domains"]:
        if any(g.match(path) for g in patterns):
            return name, risk
    # No domain matches: the top-level directory still says something useful about scope. Slugged
    # like a configured domain's name (repo.schema.json), since finder runs are named bugs-<domain>.
    top = path.split("/", 1)[0] if "/" in path else ""
    slug = re.sub(r"[^a-z0-9]+", "-", top.lower()).strip("-")
    slug = re.sub(r"^[^a-z]+", "", slug)[:31].rstrip("-")
    return slug or "root", "medium"


def review_scope(rounds_gh, delta, delta_from_round, max_rounds, full):
    """What this round reviews, from the bot's rounds on GitHub (meta.rounds) and the delta.
    The round number is the bot reviews on the PR plus one. Scope, in order:
      full       first round, --full, or no delta could be computed;
      unchanged  nothing to review again: no commit since the last bot review, or commits that did
                 not change the PR's own diff (a sync with the base);
      capped     past review.max_rounds: the bot stops and suggests rethinking the approach;
      delta      from review.delta_from_round on: only the files whose patch changed;
      full       otherwise."""
    rounds_gh = rounds_gh or {"count": 0, "last": None, "commits_since": None}
    last = rounds_gh.get("last")
    number = rounds_gh.get("count", 0) + 1
    previous = last["head"] if last else None
    info = {"number": number, "previous_head": previous, "scope": "full", "full_forced": bool(full),
            "commits_since": rounds_gh.get("commits_since")}
    if not last:
        info["note"] = "first round: no bot review on the PR yet, the whole diff"
        return info
    usable = bool(delta) and not delta.get("unavailable") and delta.get("from_head") == previous
    if rounds_gh.get("commits_since") == 0 or (usable and not delta["files"]):
        info["scope"] = "unchanged"
        info["note"] = ("no commit since the last bot review" if rounds_gh.get("commits_since") == 0
                        else "commits since the last bot review did not change the PR (only a sync with the base)")
        if not full:
            return info
        info["scope"] = "full"
    if full:
        info["note"] = "--full: the whole diff"
        return info
    if number > max_rounds:
        info["scope"] = "capped"
        info["note"] = (f"round {number} is past review.max_rounds ({max_rounds}): step back and reconsider the "
                        "approach with the author; --full reviews anyway")
        return info
    if number >= delta_from_round:
        if usable:
            info["scope"] = "delta"
            info["note"] = f"only what changed in the PR since {previous[:9]}: {len(delta['files'])} file(s)"
        else:
            info["note"] = "no delta available: the whole diff" + (f" ({delta['unavailable']})" if delta and delta.get("unavailable") else "")
        return info
    info["note"] = f"round {number}: the whole diff (delta from round {delta_from_round})"
    return info


def compute(args):
    meta = read_json(args.meta)
    if not meta:
        fail(f"unreadable meta.json: {args.meta}")
    try:
        config = repoconfig.load(args.config)
    except repoconfig.ConfigError as error:
        fail(str(error))
    live = read_json(args.state) or {}
    ci = {
        "read": bool(live.get("checks_read")),
        "error": live.get("checks_error"),
        "all": checks_summary(live.get("checks", [])),
        "required": dict(checks_summary(live.get("required", [])),
                         available=bool(live.get("required_available"))),
    }
    with open(args.hooks, encoding="utf-8") as handle:
        hooks = json.load(handle)
    # Repository overrides (repos/<owner>/<name>.json `hooks`) replace the global value per key.
    override = config["hooks"].get("reviewers", {})
    if "max" in override:
        hooks["reviewers"] = dict(hooks["reviewers"], max=override["max"])
        hooks["overrides"] = {"reviewers.max": config["path"]}
    live = {"labels": live.get("labels", []), "draft": bool(live.get("draft", meta.get("draft"))),
            "state": live.get("state", meta.get("state")), "ci": ci,
            "reviewers": count_reviewers(live.get("reviews", []), meta.get("author"),
                                         live.get("me"), hooks["reviewers"]["ignore"]),
            "requested": requested_of_me(live.get("requests", []), live.get("me"), live.get("teams"))
                         if hooks["exclude_teams"] else [],
            "last_commit_at": live.get("last_commit_at"),
            "idle_days": idle_days(live.get("last_commit_at"))}
    previous = read_json(args.out)
    if previous and "rounds" not in previous:
        previous = None

    large_diff = config["large_diff"] or args.large_diff
    files, ignored, binaries = [], [], []
    for entry in meta["files"]:
        path = entry["path"]
        if any(g.match(path) for g in config["ignore"]):
            ignored.append(path)
            continue
        test = any(g.match(path) for g in config["tests"])
        # A test file never sets a floor: it checks the sensitive code, it does not change it.
        sensitive = [] if test else [s for s in config["sensitive"] if s["glob"].match(path)
                     or (entry.get("previous_path") and s["glob"].match(entry["previous_path"]))]
        # No finder can read a binary: it leaves the review like `ignore`, unless it is sensitive,
        # where it must still set the floor and reach a human.
        if entry.get("binary") and not sensitive:
            binaries.append(path)
            continue
        domain, risk = domain_of(path, config)
        files.append({
            "path": path,
            "status": entry["status"],
            "lines": (entry.get("additions") or 0) + (entry.get("deletions") or 0),
            "binary": bool(entry.get("binary")),
            "domain": domain,
            "risk": risk,
            "test": test,
            "sensitive": [{"glob": s["glob"].pattern, "reason": s["reason"]} for s in sensitive],
            "local_exec": "deny" if any(s["local_exec"] == "deny" for s in sensitive) else "allow",
        })

    lines = sum(f["lines"] for f in files)
    production = [f for f in files if f["risk"] != "low" and not f["test"]]
    prod_lines = sum(f["lines"] for f in production)
    test_lines = sum(f["lines"] for f in files if f["test"])
    prod_domains = sorted({f["domain"] for f in production})
    broad_change = args.broad_change
    reasons, floor = [], None

    def up(why):
        nonlocal tier
        if tier != "high":
            tier = TIERS[TIERS.index(tier) + 1]
            reasons.append(why)

    if not files:
        tier = "low"
        reasons.append("no reviewable file (all ignored or binary)")
    else:
        # Tests take the risk of their domain only when nothing else changed: a PR with code and
        # tests is as risky as its code, a tests-only PR is low.
        weighed = [f for f in files if not f["test"]] or [dict(f, risk="low") for f in files]
        tier = max((f["risk"] for f in weighed), key=TIERS.index)
        by_risk = sorted({f["domain"] for f in weighed if f["risk"] == tier})
        reasons.append(f"{tier}-risk domain: {', '.join(by_risk)}" if config["domains"]
                       else "regular code change")
    sensitive_hit = sorted({s["reason"] or s["glob"] for f in files for s in f["sensitive"]})
    if sensitive_hit:
        tier, floor = "high", "deep"
        reasons.append("sensitive: " + "; ".join(sensitive_hit))
    if prod_lines > large_diff:
        up(f"large diff: {prod_lines} production lines (limit {large_diff})")
    if broad_change and len(prod_domains) >= broad_change:
        up(f"broad change: {len(prod_domains)} domains ({', '.join(prod_domains[:4])})")

    scope = {}
    for f in files:
        bucket = scope.setdefault(f["domain"], {"files": 0, "lines": 0})
        bucket["files"] += 1
        bucket["lines"] += f["lines"]

    warnings = []
    if "domains" not in config["configured"]:
        warnings.append("repo config: domains not configured, every file medium risk, scope by top-level directory")
    if "sensitive" not in config["configured"]:
        warnings.append("repo config: sensitive not configured, no file sets a floor")
    if "tests" in config["configured"] and production and not test_lines:
        warnings.append(f"production code changed ({prod_lines} lines), no test file changed")
    knowledge = meta.get("knowledge") or {}
    if knowledge.get("changed_by_pr"):
        warnings.append("review instructions changed by this PR: " + ", ".join(knowledge["changed_by_pr"][:3])
                        + " (this round uses the base version)")
    if not any(f["kind"] == "review" for f in knowledge.get("files", [])) and files:
        warnings.append("no REVIEW.md covers the changed files (review uses the bot's defaults)")
    if knowledge.get("bytes", 0) > 150000:
        warnings.append(f"review instructions total {knowledge['bytes'] // 1000} KB: large context for every finder and verifier")
    if live["draft"]:
        warnings.append("PR is a draft")
    if live["state"] != "OPEN":
        warnings.append(f"PR is {str(live['state']).lower()}")
    if not ci["read"]:
        warnings.append(f"checks unreadable: {ci['error']}")
    optional_failing = sorted(set(ci["all"]["failing"]) - set(ci["required"]["failing"])) \
        if ci["required"]["available"] else []
    if optional_failing:
        warnings.append(f"optional check(s) failing, not gating: {', '.join(optional_failing[:3])}")
    if ignored:
        warnings.append(f"{len(ignored)} file(s) ignored by the repo config")
    if binaries:
        warnings.append(f"{len(binaries)} binary file(s) not reviewed")
    sensitive_binaries = [f["path"] for f in files if f["binary"]]
    if sensitive_binaries:
        warnings.append("sensitive binary file(s), check by hand: " + ", ".join(sensitive_binaries[:3]))

    deny = sorted({f["path"] for f in files if f["local_exec"] == "deny"})
    outcome, rule, why = evaluate_hooks(hooks, live)

    rounds = list((previous or {}).get("rounds", []))
    head = meta["head"]["sha"]
    if not rounds or rounds[-1]["head"] != head:
        rounds.append({"head": head, "at": now(), "decision": None})
    round_info = review_scope(meta.get("rounds"), meta.get("delta"), args.delta_from_round, args.max_rounds, args.full)

    triage = {
        "version": 1,
        "repo": meta["repo"],
        "pr": meta["pr"],
        "head": head,
        "author": meta.get("author"),
        "config": {"path": config["path"], "exists": config["exists"], "configured": config["configured"]},
        "tier": tier,
        "reasons": reasons,
        "depth": {"suggested": DEPTH_FOR[tier], "floor": floor},
        "stats": {"files": len(files), "lines": lines, "production": prod_lines, "tests": test_lines,
                  "domains": prod_domains, "ignored": len(ignored), "binary": len(binaries), "large_diff": large_diff,
                  "broad_change": broad_change},
        "scope": dict(sorted(scope.items(), key=lambda kv: (-kv[1]["lines"], kv[0]))),
        "local_exec": {"allowed": not deny, "denied_by": deny},
        "ci": ci,
        "hooks": {"outcome": outcome, "rule": rule, "reason": why, "labels": live["labels"],
                  "draft": live["draft"], "reviewers": live["reviewers"],
                  "requested": live["requested"], "last_commit_at": live["last_commit_at"],
                  "config": hooks},
        "round": round_info,
        "warnings": warnings,
        "files": files,
        "rounds": rounds,
        "computed_at": now(),
    }
    write_atomic(args.out, triage)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def assess(args):
    """Record the triage agent's assessment for the current head (rounds[-1].agent).

    The rules give tier and depth from facts; the agent reads the change and may keep, raise or
    lower them. Limits enforced here, whatever the agent was told:
      - raising is free;
      - lowering moves at most one level, for tier and for depth;
      - never below the floor a `sensitive` file sets (depth), nor below high tier when there is
        one: that floor is a declaration by whoever owns the repository, not a rule to weigh;
      - one direction per assessment: raising one and lowering the other is refused;
      - a reason is required, one line, about the change (it is shown on the card and kept for
        the retro, which compares rules, agent and the reviewer's decision).
    """
    triage = read_json(args.out)
    if not triage:
        fail("triage.json missing")
    if args.tier not in TIERS:
        fail(f"invalid tier: {args.tier}")
    if args.depth not in DEPTHS:
        fail(f"invalid depth: {args.depth}")
    reason = " ".join((args.reason or "").split())
    if not reason:
        fail("a reason is required")
    if len(reason) > 300:
        fail("reason longer than 300 characters: keep it to one line about the change")

    rules_tier, rules_depth, floor = triage["tier"], triage["depth"]["suggested"], triage["depth"]["floor"]
    dt = TIERS.index(args.tier) - TIERS.index(rules_tier)
    dd = DEPTHS.index(args.depth) - DEPTHS.index(rules_depth)
    if (dt > 0 and dd < 0) or (dt < 0 and dd > 0):
        fail("raising one of tier/depth and lowering the other is not an assessment: pick a direction")
    if dt < -1 or dd < -1:
        fail(f"lowering moves at most one level (rules: {rules_tier}/{rules_depth})")
    if floor and DEPTHS.index(args.depth) < DEPTHS.index(floor):
        fail(f"depth {args.depth} is below the floor ({floor}) a sensitive file sets: only the reviewer can go below it")
    if floor and dt < 0:
        fail("a sensitive file keeps the tier high: only the reviewer can go below it")

    outcome = "keep" if dt == 0 and dd == 0 else ("raise" if dt > 0 or dd > 0 else "lower")
    triage["rounds"][-1]["agent"] = {
        "outcome": outcome,
        "tier": {"rules": rules_tier, "final": args.tier},
        "depth": {"rules": rules_depth, "final": args.depth},
        "reason": reason,
        "at": now(),
    }
    meta = read_json(os.path.join(os.path.dirname(args.out), "meta.json")) or {}
    if meta.get("repo"):
        run = harness.runs(meta["repo"], meta["pr"], meta.get("fetched_at")).get("triage")
        if run:
            triage["rounds"][-1]["agent"].update(run)
    write_atomic(args.out, triage)
    print(outcome)


def suggested_depth(triage):
    """The depth to suggest for the current head: the triage agent's, when it assessed this head,
    else the rules'."""
    agent = triage["rounds"][-1].get("agent")
    return agent["depth"]["final"] if agent else triage["depth"]["suggested"]


def decide(args):
    """Record what the reviewer chose for the current head; the retro compares it later."""
    triage = read_json(args.out)
    if not triage:
        fail("triage.json missing")
    if args.depth not in DEPTHS + ("abort",):
        fail(f"invalid depth: {args.depth}")
    suggested = suggested_depth(triage)
    current = triage["rounds"][-1]
    chosen = args.depth in DEPTHS
    forced = triage["hooks"]["outcome"] != "review"
    current["decision"] = {
        "depth": args.depth,
        "suggested": suggested,
        "override": chosen and args.depth != suggested,
        "below_floor": chosen and bool(triage["depth"]["floor"])
                       and DEPTHS.index(args.depth) < DEPTHS.index(triage["depth"]["floor"]),
        "forced": forced,
        "at": now(),
    }
    triage["decision"] = current["decision"]
    write_atomic(args.out, triage)


def state(args):
    """state.json from what the command read with gh: view.json, checks.json, required.json."""
    def load(name, default):
        try:
            with open(os.path.join(args.dir, name), encoding="utf-8") as handle:
                text = handle.read().strip()
            return json.loads(text) if text else default
        except (OSError, ValueError):
            return default

    view = load("view.json", {})
    with open(os.path.join(args.dir, "state.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "labels": sorted(label["name"] for label in view.get("labels", [])),
            "draft": bool(view.get("isDraft")),
            "state": view.get("state"),
            "checks_read": args.read == "true",
            "checks_error": ((args.error or "").strip().splitlines() or [None])[0] if args.read != "true" else None,
            "checks": load("checks.json", []),
            "required": load("required.json", []),
            "required_available": args.available == "true",
            "reviews": load("reviews.json", []),
            "me": (load("me.json", {}) or {}).get("login"),
            "requests": view.get("reviewRequests", []),
            "last_commit_at": ((view.get("commits") or [{}])[-1]).get("committedDate"),
            "teams": load("teams.json", []),
        }, handle)


def reviewers_max(args):
    """Effective hooks.reviewers.max for a repository: its config override, else the global."""
    try:
        config = repoconfig.load(args.config)
    except repoconfig.ConfigError as error:
        fail(str(error))
    print(config["hooks"].get("reviewers", {}).get("max", args.global_max))


def state_note(args):
    """One line for the board: checks read, required ones, labels, draft."""
    data = read_json(os.path.join(args.dir, "state.json")) or {}
    parts = [f"{len(data.get('checks', []))} check(s)"]
    parts.append(f"{len(data.get('required', []))} required" if data.get("required_available")
                 else "no required checks")
    if data.get("labels"):
        parts.append("labels: " + ", ".join(data["labels"]))
    if data.get("draft"):
        parts.append("draft")
    if not data.get("checks_read"):
        parts.append("checks unreadable")
    print(" · ".join(parts))


def ci_line(ci):
    """'required 17/17 pass · all: 26 pass, 1 fail, 2 skipping' — buckets named as gh names them."""
    if not ci["read"]:
        return f"unreadable: {ci['error']}"

    def counts(c):
        return ", ".join(f"{c[b]} {b}" for b in BUCKETS if c[b]) or "none"

    required = ci["required"]
    head = f"required {required['pass']}/{required['total']} pass" if required["available"] \
        else "no required checks"
    return f"{head} · all: {counts(ci['all'])}"


def card(args):
    """Fields for the shell, NUL-separated: tier, suggested depth (the agent's when it assessed this
    head), floor, reasons (joined), files, lines, round, previous head, round note, local_exec
    allowed, denied paths, ci state, ci counters, config exists, decision depth, hook outcome,
    hook rule, hook reason, labels, agent outcome, agent line, then scope rows and warnings."""
    t = read_json(args.out)
    scope = [f"{name}\t{v['files']}\t{v['lines']}" for name, v in t["scope"].items()]
    ci = t["ci"]
    agent = t["rounds"][-1].get("agent") or {}
    fields = [
        t["tier"], suggested_depth(t), t["depth"]["floor"] or "",
        " · ".join(t["reasons"]), str(t["stats"]["files"]), str(t["stats"]["lines"]),
        str(t["round"]["number"]), t["round"]["previous_head"] or "",
        f'{t["round"]["scope"]}\t{t["round"].get("note", "")}',
        "1" if t["local_exec"]["allowed"] else "0", ", ".join(t["local_exec"]["denied_by"][:3]),
        ci["required"]["state"] if ci["required"]["available"] else ("none" if ci["read"] else "unreadable"),
        ci_line(ci),
        "1" if t["config"]["exists"] else "0",
        (t["rounds"][-1].get("decision") or {}).get("depth") or "",
        t["hooks"]["outcome"], t["hooks"]["rule"] or "", t["hooks"]["reason"] or "",
        ", ".join(t["hooks"]["labels"]),
        agent.get("outcome", ""),
        f"{agent['tier']['rules']}/{agent['depth']['rules']} → {agent['tier']['final']}/{agent['depth']['final']}  {agent['reason']}"
        if agent else "",
        str(len(scope)), *scope, str(len(t["warnings"])), *t["warnings"],
    ]
    sys.stdout.write("\0".join(fields) + "\0")


def main():
    parser = argparse.ArgumentParser(prog="triage.py")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("compute")
    sub.add_argument("--meta", required=True)
    sub.add_argument("--config", required=True)
    sub.add_argument("--state", required=True)
    sub.add_argument("--hooks", required=True)
    sub.add_argument("--out", required=True)
    sub.add_argument("--large-diff", type=int, required=True)
    sub.add_argument("--broad-change", type=int, default=0)
    sub.add_argument("--delta-from-round", type=int, default=3)
    sub.add_argument("--max-rounds", type=int, default=3)
    sub.add_argument("--full", action="store_true")
    sub.set_defaults(handler=compute)

    sub = commands.add_parser("assess")
    sub.add_argument("--out", required=True)
    sub.add_argument("--tier", required=True)
    sub.add_argument("--depth", required=True)
    sub.add_argument("--reason", required=True)
    sub.set_defaults(handler=assess)

    sub = commands.add_parser("state")
    sub.add_argument("--dir", required=True)
    sub.add_argument("--read", required=True)
    sub.add_argument("--error", default="")
    sub.add_argument("--available", required=True)
    sub.set_defaults(handler=state)

    sub = commands.add_parser("reviewers-max")
    sub.add_argument("--config", required=True)
    sub.add_argument("--global", dest="global_max", type=int, required=True)
    sub.set_defaults(handler=reviewers_max)

    sub = commands.add_parser("state-note")
    sub.add_argument("--dir", required=True)
    sub.set_defaults(handler=state_note)

    sub = commands.add_parser("decide")
    sub.add_argument("--out", required=True)
    sub.add_argument("--depth", required=True)
    sub.set_defaults(handler=decide)

    sub = commands.add_parser("card")
    sub.add_argument("--out", required=True)
    sub.set_defaults(handler=card)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
