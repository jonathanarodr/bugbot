#!/usr/bin/env python3
"""Append a validated retrospective summary to the permanent per-PR JSONL record."""

import argparse
import datetime
import fcntl
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import schema  # noqa: E402


def fail(message):
    print(f"bugbot retro: {message}", file=sys.stderr)
    raise SystemExit(1)


def record(args):
    findings = schema.read_document(args.findings, "findings")
    try:
        report = json.load(sys.stdin)
    except ValueError as error:
        fail(f"invalid report JSON: {error}")
    if not isinstance(report, dict) or set(report) != {"summary", "candidates"}:
        fail("report must contain exactly 'summary' and 'candidates'")

    event = {
        "version": 1,
        "type": "retrospective",
        "repo": findings["repo"],
        "pr": findings["pr"],
        "head": findings["head"],
        "round": findings["round"]["number"],
        "verdict": findings["verdict"],
        "trigger": args.trigger,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **report,
    }
    try:
        schema.validate(event, "retro-record")
    except schema.ValidationError as error:
        fail("invalid retrospective report: " + "; ".join(error.errors[:5]))

    parts = event["repo"].split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..") for part in parts):
        fail("repository name cannot be used as a retro path")
    target_dir = os.path.join(os.path.abspath(args.retro_dir), parts[0].lower(), parts[1].lower())
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"pr-{event['pr']}.jsonl")
    encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    with open(target, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        if args.trigger == "auto_approve":
            for line in handle:
                try:
                    prior = json.loads(line)
                except ValueError:
                    fail(f"{target}: malformed existing JSONL record")
                if (prior.get("type") == "retrospective"
                        and prior.get("trigger") == "auto_approve"
                        and prior.get("head") == event["head"]
                        and prior.get("round") == event["round"]):
                    print(f"already recorded: {event['repo']}#{event['pr']} round {event['round']}")
                    return
        handle.seek(0, os.SEEK_END)
        handle.write(encoded.decode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    print(f"recorded: {event['repo']}#{event['pr']} round {event['round']} · {target}")


def main():
    parser = argparse.ArgumentParser(prog="retro/record.py")
    parser.add_argument("--findings", required=True)
    parser.add_argument("--retro-dir", required=True)
    parser.add_argument("--trigger", choices=("auto_approve", "manual"), required=True)
    args = parser.parse_args()
    record(args)


if __name__ == "__main__":
    main()
