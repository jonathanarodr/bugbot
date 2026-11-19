#!/usr/bin/env python3
"""bugbot inbox: open PRs whose review was requested from you, filtered by the hooks.

The hooks are applied in two places, each where it is cheapest and exact:

  in the search query   draft: false        → draft:false
                        stale_days          → updated:>=<today - N> (a lower bound: a PR with a
                                              recent commit was updated then too; the exact
                                              head-commit check runs on each result)
                        labels.skip         → -label:"x" for each
                        labels.trigger      → label:"a","b" (any of them)
                        the PR author       → -author:@me (GitHub never asks you to review your own)
  on each result        stale_days, exclude_teams, reviewers.max, ci — the same functions `bugbot
                        triage` runs (lib/triage/triage.py), so the list and the triage card cannot
                        disagree

One paginated GraphQL search returns every PR with its review requests and reviews. Required
checks need `isRequired(pullRequestNumber:)`, an argument a search cannot vary per result, so the
PRs that survive the other hooks get a second query: one GraphQL request per 20 PRs, aliased, the
same field `gh pr checks --required` reads. Checks are bucketed and de-duplicated like gh (latest
run per name), so `ci: passed` hides a failed required check (skip) and a running one (wait).

Already decided: a PR whose current head has a recorded decision in the cache (triage.json
rounds[]) is left out, so a re-review shows up again only after new commits.

  query   print the search string for the settings and workspace
  list    run the search with gh and print one JSON object (schemas/inbox.schema.json)

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))
sys.path.insert(0, os.path.join(HERE, "..", "triage"))
import repoconfig  # noqa: E402
import schema  # noqa: E402
import triage  # noqa: E402

GRAPHQL = """
query($q: String!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 50, after: $endCursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url isDraft createdAt updatedAt headRefOid
        author { login }
        repository { nameWithOwner }
        labels(first: 50) { nodes { name } }
        reviewRequests(first: 50) {
          nodes { requestedReviewer { __typename ... on User { login } ... on Team { slug organization { login } } } }
        }
        reviews(last: 100) { nodes { state author { __typename login } } }
        commits(last: 1) { nodes { commit { committedDate } } }
      }
    }
  }
}
"""

# Check state → gh bucket (cli/cli pkg/cmd/pr/checks/aggregate.go), the vocabulary triage uses.
BUCKET = {"SUCCESS": "pass", "ERROR": "fail", "FAILURE": "fail", "TIMED_OUT": "fail",
          "ACTION_REQUIRED": "fail", "CANCELLED": "cancel", "SKIPPED": "skipping", "NEUTRAL": "skipping"}
CHECKS_PER_QUERY = 20
CONTEXTS = """
commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) {
  totalCount
  nodes {
    __typename
    ... on CheckRun { name status conclusion startedAt isRequired(pullRequestNumber: %(n)d) }
    ... on StatusContext { context state createdAt isRequired(pullRequestNumber: %(n)d) }
  }
} } } } }
"""


def fail(message):
    print(f"inbox.py: {message}", file=sys.stderr)
    sys.exit(1)


def quote(label):
    """GitHub search label term. Quotes are not escapable in search syntax, so a label holding
    one cannot be expressed and is reported instead of silently matching something else."""
    if '"' in label:
        fail(f'label with a double quote cannot be searched: {label}')
    return f'"{label}"'


def build_query(hooks, repos, today=None):
    terms = ["is:pr", "is:open", "archived:false", "review-requested:@me", "-author:@me"]
    if not hooks["draft"]:
        terms.append("draft:false")
    if hooks["stale_days"]:
        today = today or datetime.date.today()
        terms.append(f"updated:>={(today - datetime.timedelta(days=hooks['stale_days'])).isoformat()}")
    terms += [f"-label:{quote(label)}" for label in hooks["labels"]["skip"]]
    if hooks["labels"]["trigger"]:
        terms.append("label:" + ",".join(quote(label) for label in hooks["labels"]["trigger"]))
    terms += [f"repo:{repo}" for repo in repos]
    return " ".join(terms)


def search(query):
    command = ["gh", "api", "graphql", "--paginate", "--slurp", "-f", f"query={GRAPHQL}", "-f", f"q={query}"]
    env = dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1")
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        fail("search failed: " + (result.stderr.strip().splitlines() or ["gh exited " + str(result.returncode)])[-1])
    pages = json.loads(result.stdout)
    errors = [e.get("message", "") for page in pages for e in page.get("errors") or []]
    if errors:
        fail("search failed: " + errors[0])
    return [node for page in pages for node in page["data"]["search"]["nodes"] if node]


def gh_graphql(query):
    env = dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1")
    result = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True, text=True, env=env)
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    if result.returncode != 0 and not data.get("data"):
        return None, (result.stderr.strip().splitlines() or ["gh exited " + str(result.returncode)])[-1]
    return data.get("data") or {}, None


def required_checks(prs):
    """{(repo, number): (checks, error)} with checks as [{"name", "bucket"}] of the required ones,
    the latest run per name, as `gh pr checks --required` reports them."""
    out = {}
    for start in range(0, len(prs), CHECKS_PER_QUERY):
        chunk = prs[start:start + CHECKS_PER_QUERY]
        parts = []
        for index, (repo, number) in enumerate(chunk):
            owner, name = repo.split("/", 1)
            parts.append(f"p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
                         f"{{ pullRequest(number: {int(number)}) {{ {CONTEXTS % {'n': int(number)}} }} }}")
        data, error = gh_graphql("query { " + " ".join(parts) + " }")
        for index, key in enumerate(chunk):
            node = ((data or {}).get(f"p{index}") or {}).get("pullRequest")
            if error or not node:
                out[key] = ([], error or "pull request not readable")
                continue
            commit = ((node["commits"]["nodes"] or [{}])[0].get("commit") or {})
            contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {"nodes": [], "totalCount": 0}
            if contexts["totalCount"] > len(contexts["nodes"]):
                out[key] = ([], f"{contexts['totalCount']} checks, more than one page")
                continue
            latest = {}
            for c in contexts["nodes"]:
                if not c.get("isRequired"):
                    continue
                if c["__typename"] == "CheckRun":
                    name, when = c["name"], c.get("startedAt") or ""
                    state = c["conclusion"] if c["status"] == "COMPLETED" else c["status"]
                else:
                    name, when, state = c["context"], c.get("createdAt") or "", c["state"]
                if name not in latest or when > latest[name][0]:
                    latest[name] = (when, BUCKET.get(state, "pending"))
            out[key] = ([{"name": n, "bucket": b} for n, (_w, b) in sorted(latest.items())], None)
    return out


def gh_json(*args):
    env = dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1")
    result = subprocess.run(["gh", "api", *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        fail(f"gh api {args[-1]} failed: " + (result.stderr.strip().splitlines() or [""])[-1])
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def decided_heads(data_dir, repo, number):
    """Heads of this PR with a recorded decision (triage.json rounds[] whose decision is set).
    A new head whose PR diff did not change (a sync with the base) is a new head here; triage
    then reports the round as unchanged."""
    owner, name = repo.lower().split("/", 1)
    path = os.path.join(data_dir, "cache", owner, name, f"pr-{number}", "triage.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return set()
    return {r["head"] for r in data.get("rounds", []) if r.get("decision")}


def requests_of(node):
    out = []
    for entry in node["reviewRequests"]["nodes"]:
        reviewer = entry.get("requestedReviewer") or {}
        if reviewer.get("__typename") == "Team":
            out.append({"__typename": "Team", "slug": f'{reviewer["organization"]["login"]}/{reviewer["slug"]}'})
        elif reviewer.get("__typename") == "User":
            out.append({"__typename": "User", "login": reviewer["login"]})
    return out


def reviews_of(node):
    """GraphQL reviews in the shape count_reviewers expects (REST: user.login, user.type)."""
    return [{"user": {"login": r["author"]["login"], "type": r["author"]["__typename"]}, "state": r["state"]}
            for r in node["reviews"]["nodes"] if r.get("author")]


def age_days(stamp, now):
    created = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return max(0, (now - created).days)


def head_commit(node):
    return ((node["commits"]["nodes"] or [{}])[0].get("commit") or {})


def evaluate(node, hooks, home, me, teams, checks=None):
    """(outcome, rule, reason, requested, reviewers, max, ci) for one PR, with repository
    overrides. `checks` is (required checks, read error); None skips the ci hook (first pass)."""
    repo = node["repository"]["nameWithOwner"]
    try:
        config = repoconfig.load(repoconfig.path_for(home, repo))
    except repoconfig.ConfigError as error:
        return "skip", "config", f"invalid repo config: {str(error).splitlines()[0]}", [], [], 0, None
    effective = json.loads(json.dumps(hooks))
    override = config["hooks"].get("reviewers", {})
    if "max" in override:
        effective["reviewers"]["max"] = override["max"]
    requested = triage.requested_of_me(requests_of(node), me, teams)
    reviewers = triage.count_reviewers(reviews_of(node), (node.get("author") or {}).get("login"), me,
                                       effective["reviewers"]["ignore"])
    live = {"draft": node["isDraft"], "labels": [l["name"] for l in node["labels"]["nodes"]],
            "requested": requested if effective["exclude_teams"] else [],
            "reviewers": reviewers,
            "idle_days": triage.idle_days(head_commit(node).get("committedDate")),
            "ci": {"read": True, "error": None, "required": {"available": False}}}
    ci = None
    if checks is not None:
        required, error = checks
        live["ci"] = {"read": error is None, "error": error,
                      "required": dict(triage.checks_summary(required), available=bool(required))}
        ci = "unread" if error else (live["ci"]["required"]["state"] if required else "none")
    else:
        effective = dict(effective, ci="any")
    outcome, rule, reason = triage.evaluate_hooks(effective, live)
    return outcome, rule, reason, requested, reviewers, effective["reviewers"]["max"], ci


def listing(args):
    with open(args.hooks, encoding="utf-8") as handle:
        hooks = json.load(handle)
    repos = [line.split("\t", 1)[0] for line in open(args.repos, encoding="utf-8") if line.strip()]
    if not repos:
        fail("no enabled repository in the workspace: run 'bugbot setup'")
    query = build_query(hooks, repos)
    nodes = search(query)

    me = gh_json("user", "--jq", "{login: .login}")[0]["login"]
    teams = [t for page in gh_json("--paginate", "user/teams", "--jq", "[.[] | .organization.login + \"/\" + .slug]")
             for t in page]

    # First pass without CI, then required checks only for the PRs still in, then the full pass:
    # the hooks decide in their own order either way, this only saves reading checks.
    survivors = [node for node in nodes
                 if evaluate(node, hooks, args.home, me, teams)[0] == "review"
                 and node["headRefOid"] not in decided_heads(args.data_dir, node["repository"]["nameWithOwner"], node["number"])]
    checks = required_checks([(n["repository"]["nameWithOwner"], n["number"]) for n in survivors])

    now = datetime.datetime.now(datetime.timezone.utc)
    items, hidden = [], []
    for node in nodes:
        repo = node["repository"]["nameWithOwner"]
        key = (repo, node["number"])
        outcome, rule, reason, requested, reviewers, limit, ci = evaluate(
            node, hooks, args.home, me, teams, checks.get(key))
        commit = head_commit(node)
        direct = any(r.lower() == me.lower() for r in requested)
        item = {
            "repo": repo, "pr": node["number"], "title": node["title"], "url": node["url"],
            "author": (node.get("author") or {}).get("login"), "head": node["headRefOid"],
            "draft": node["isDraft"], "created_at": node["createdAt"], "updated_at": node["updatedAt"],
            "age_days": age_days(node["createdAt"], now),
            "active_days": age_days(node["updatedAt"], now),
            "last_commit_at": commit.get("committedDate"),
            "idle_days": triage.idle_days(commit.get("committedDate"), now),
            "requested": requested, "direct": direct,
            "reviewers": reviewers, "reviewers_max": limit,
            "ci": ci or "unread",
        }
        if node["headRefOid"] in decided_heads(args.data_dir, repo, node["number"]):
            outcome, rule, reason = "skip", "decided", "this head already has a triage decision"
        if outcome != "review":
            item["hidden_by"] = {"rule": rule, "reason": reason}
            hidden.append(item)
        else:
            items.append(item)

    # Grouped by repository, the repository with the most recent activity first; inside a group,
    # most recent activity first (updatedAt: commits, comments, reviews).
    latest = {}
    for i in items:
        latest[i["repo"]] = max(latest.get(i["repo"], ""), i["updated_at"])
    items.sort(key=lambda i: i["updated_at"], reverse=True)
    items.sort(key=lambda i: latest[i["repo"]], reverse=True)
    hidden.sort(key=lambda i: (i["hidden_by"]["rule"], i["created_at"]))
    out = {"$schema": schema.schema_ref("inbox", args.out_dir) if args.out_dir else None,
           "version": 1, "query": query, "me": me, "at": now.isoformat(timespec="seconds"),
           "searched": len(nodes), "items": items, "hidden": hidden}
    if out["$schema"] is None:
        del out["$schema"]
    schema.validate(out, "inbox")
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def query_cmd(args):
    with open(args.hooks, encoding="utf-8") as handle:
        hooks = json.load(handle)
    repos = [line.split("\t", 1)[0] for line in open(args.repos, encoding="utf-8") if line.strip()]
    print(build_query(hooks, repos))


def rows(args):
    """TSV for the shell: kind, repo, pr, direct, age, ci, via, reviewers, title|reason, idle,
    active (days since the last activity)"""
    with open(args.file, encoding="utf-8") as handle:
        data = json.load(handle)
    for kind in ("items", "hidden"):
        for i in data[kind]:
            via = "you" if i["direct"] else ", ".join(r.split("/", 1)[-1] for r in i["requested"]) or "-"
            count = f'{len(i["reviewers"])}/{i["reviewers_max"]}' if i["reviewers_max"] else str(len(i["reviewers"]))
            last = i["title"] if kind == "items" else f'{i["hidden_by"]["rule"]}: {i["hidden_by"]["reason"]}'
            fields = [kind, i["repo"], str(i["pr"]), "1" if i["direct"] else "0", str(i["age_days"]),
                      i["ci"], via, count, last.replace("\t", " ").replace("\n", " "),
                      "" if i["idle_days"] is None else str(i["idle_days"]), str(i["active_days"])]
            print("\t".join(fields))
    print("\t".join(["summary", str(data["searched"]), str(len(data["items"])), str(len(data["hidden"]))]))


def main():
    parser = argparse.ArgumentParser(prog="inbox.py")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("query")
    sub.add_argument("--hooks", required=True)
    sub.add_argument("--repos", required=True, help="workspace.py list output")
    sub.set_defaults(handler=query_cmd)

    sub = commands.add_parser("list")
    sub.add_argument("--hooks", required=True)
    sub.add_argument("--repos", required=True, help="workspace.py list output")
    sub.add_argument("--home", required=True)
    sub.add_argument("--data-dir", required=True)
    sub.add_argument("--out-dir", default="")
    sub.set_defaults(handler=listing)

    sub = commands.add_parser("rows")
    sub.add_argument("--file", required=True)
    sub.set_defaults(handler=rows)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
