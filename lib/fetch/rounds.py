#!/usr/bin/env python3
"""Review rounds, read from the pull request on GitHub: no local state.

Every review the bot posts carries a marker in its body:

    <!-- bugbot:v1 sha=<head reviewed> round=<n> verdict=<verdict> -->

A later run lists the PR's reviews, keeps those whose author is the configured reviewer
(`review.author`: `@me` = the gh user, or a login such as `my-app[bot]`) **and** whose body holds a
marker of the current version, and reads the last one: the head it reviewed and the round number.
The author check is what makes the marker trustworthy: anyone can paste the marker in a comment,
nobody else can post as that author. A marker in a review by anyone else is ignored and reported.

`commits_since`: the commits on the PR after the last reviewed head. 0 means nothing was pushed since
that round, so there is nothing new to review; the delta tells what a push changed in the PR itself.

  marker    print the marker for a head, round and verdict (for publish)
  read      read the PR's reviews with gh and print the `rounds` object of meta.json as JSON

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import json
import os
import re
import subprocess
import sys

QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { databaseId author { login } body submittedAt state commit { oid } }
      }
    }
  }
}
"""


def marker_pattern(version):
    return re.compile(r"<!--\s*bugbot:v" + str(version) + r"\s+sha=([0-9a-f]{40})\s+round=([0-9]+)"
                      r"\s+verdict=([A-Z_]+)\s*-->")


def marker(args):
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        sys.exit("rounds.py: --sha must be a full commit sha")
    print(f"<!-- bugbot:v{args.version} sha={args.sha} round={args.round} verdict={args.verdict} -->")


def gh(*args):
    env = dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1")
    result = subprocess.run(["gh", *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip().splitlines() or ["gh exited " + str(result.returncode)])[-1])
    return result.stdout


def read(args):
    author = args.author
    if author == "@me":
        author = json.loads(gh("api", "user", "--jq", "{login: .login}"))["login"]
    owner, name = args.repo.split("/", 1)
    pages = json.loads(gh("api", "graphql", "--paginate", "--slurp", "-f", f"query={QUERY}",
                          "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={args.pr}"))
    reviews = [n for page in pages for n in page["data"]["repository"]["pullRequest"]["reviews"]["nodes"]]
    pattern = marker_pattern(args.version)

    found, foreign = [], []
    for review in reviews:
        match = pattern.search(review.get("body") or "")
        if not match:
            continue
        login = (review.get("author") or {}).get("login") or ""
        if login.lower() != author.lower():
            foreign.append(login)
            continue
        found.append({"id": review["databaseId"], "head": match.group(1), "round": int(match.group(2)),
                      "verdict": match.group(3), "at": review.get("submittedAt"),
                      "commit": (review.get("commit") or {}).get("oid")})
    found.sort(key=lambda r: r["at"] or "")
    last = found[-1] if found else None

    commits_since = None
    if last and args.head:
        if last["head"] == args.head:
            commits_since = 0
        else:
            out = subprocess.run(["git", "-C", args.clone, "-c", "core.hooksPath=/dev/null", "rev-list", "--count",
                                  f"{last['head']}..{args.head}"], capture_output=True, text=True)
            commits_since = int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None

    print(json.dumps({
        "author": author,
        "count": len(found),
        "last": last,
        "commits_since": commits_since,
        "ignored_foreign": sorted(set(foreign)),
    }))


def main():
    parser = argparse.ArgumentParser(prog="rounds.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("marker")
    sub.add_argument("--version", type=int, required=True)
    sub.add_argument("--sha", required=True)
    sub.add_argument("--round", type=int, required=True)
    sub.add_argument("--verdict", required=True)
    sub.set_defaults(handler=marker)
    sub = commands.add_parser("read")
    sub.add_argument("--repo", required=True)
    sub.add_argument("--pr", type=int, required=True)
    sub.add_argument("--author", required=True)
    sub.add_argument("--version", type=int, required=True)
    sub.add_argument("--head", default="")
    sub.add_argument("--clone", default="")
    sub.set_defaults(handler=read)
    args = parser.parse_args()
    try:
        args.handler(args)
    except RuntimeError as error:
        print(f"rounds: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
