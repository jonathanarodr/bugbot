#!/usr/bin/env python3
"""bugbot findings: assemble a PR round's findings.json from the finders' candidates and the
verifiers' verdicts. Deterministic: counting verdicts and writing JSON is no job for a model.

  candidates/<finder>.json              what each finder raised (schemas/candidates.schema.json)
  verdicts/<finder>.<index>.<verifier>.json  one verdict each (schemas/verdicts.schema.json)
  → findings.json                       every candidate, kept and cut, with its verdicts
                                        (schemas/findings.schema.json)

The agents write only judgment; what is known without judgment is filled in here: the finder from
the file name, each candidate's domain from triage.json files[], the domains a run covered from its
name (bugs-<domain>), the candidate and verifier of a verdict from its file name.

Rules, in order:
  1. Only files for the PR's current head count: fetch empties candidates/ and verdicts/ when the head
     changes. A verdict older than its candidates file is refused: the finder ran again after it, and
     the index may point at another candidate.
  2. Every candidate needs the number of verdicts its depth asks for (quick 1, standard 2, deep 2, or 3
     when the first two disagree); a missing verdict fails the assembly unless --partial, which keeps
     the candidate as cut with the reason "not verified".
  3. Outcome: keep when keep verdicts are a strict majority of the verdicts cast; otherwise cut (a tie,
     unsure verdicts, or no verdicts). Unsure never counts as keep.
  4. Severity of a kept finding: the highest severity supported by a keep verdict, else the
     candidate's. A higher severity from one verifier is not averaged away by a lower one.
     pre_existing: true when any keep verdict said from_pr = pre_existing.
  5. Duplicates: among kept findings, two with the same file and title (case and space
     insensitive), or a verdict's duplicate_of pointing at a kept one, keep only the first.
  6. Order: Critical, High, Medium, Low; within each severity, kept before cut, new before
     pre-existing, then file and line. Ids F1, F2... follow that order.
  6b. Low cap (review.max_lows, --max-lows; 0: no limit): past the cap, kept Lows are cut with the
     reason "over review.max_lows". Carried Lows (already posted) take their places first, then the
     others in rank order. Critical, High and Medium findings are never cut by it.
  7. Verdict: REQUEST_CHANGES with a kept Critical/High that is not pre-existing; COMMENT with a
     kept Medium or pre-existing Critical/High; PASS with only Low findings or nothing kept.
  8. Rounds (triage.json `round.scope`): `delta` carries the previous round's kept findings on
     files the PR did not change since then (meta.delta.files), unverified again, marked
     `carried_from`; `unchanged` carries all of them and needs no candidates; `capped` assembles
     nothing; `full` reviews everything again and carries nothing. The previous round's file is
     previous-findings.json, kept by fetch when the head changed.

  assemble   write findings.json (atomic) and print a TSV summary for the shell
  rows       TSV rows of an existing findings.json, for the preview

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))
import harness  # noqa: E402
import schema  # noqa: E402

VERDICTS_FOR = {"quick": 1, "standard": 2, "deep": 2}
SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
CANDIDATES_FILE = re.compile(schema.load_schema("candidates")["x-file"])
VERDICT_FILE = re.compile(schema.load_schema("verdicts")["x-file"])


def fail(message, code=1):
    print(f"findings: {message}", file=sys.stderr)
    sys.exit(code)


def read(path, name):
    try:
        return schema.read_document(path, name)
    except schema.ValidationError as error:
        fail(f"{os.path.basename(path)}: " + "; ".join(error.errors[:3]))


def write(path, data):
    """Rewrite an agent's file in place (only to add `agent`), valid against its schema."""
    name = "verdicts" if os.path.basename(os.path.dirname(path)) == "verdicts" else "candidates"
    schema.validate(data, name)
    fd, temp = tempfile.mkstemp(prefix=".stamp.", suffix=".json", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, path)


def norm(text):
    return " ".join((text or "").lower().split())


def load_round(meta, triage, scope):
    head = meta["head"]["sha"]
    decision = (triage["rounds"][-1].get("decision") or {}) if triage["rounds"][-1]["head"] == head else {}
    depth = decision.get("depth")
    if depth not in VERDICTS_FOR:
        fail("no review depth recorded for this head: run 'bugbot triage <pr> --depth ...' first", 2)

    ran = harness.runs(meta["repo"], meta["pr"], meta.get("fetched_at"))
    domain_of = {f["path"]: f["domain"] for f in triage["files"]}
    domains = {f["domain"] for f in triage["files"]}
    candidates, written = {}, {}
    for path in sorted(glob.glob(os.path.join(meta["paths"]["candidates"], "*.json"))):
        name = os.path.basename(path)[:-len(".json")]
        if not CANDIDATES_FILE.match(os.path.basename(path)):
            fail(f"candidates/{name}.json: the file name is the finder run's name: {CANDIDATES_FILE.pattern}")
        data = read(path, "candidates")
        harness.stamp(path, data, ran.get(f"candidates/{name}"), write)
        for candidate in data["candidates"]:
            candidate["domain"] = domain_of.get(candidate["file"], "other")
            candidate["finder"] = name
        covered = name[len("bugs-"):] if name.startswith("bugs-") else None
        data["domains"] = [covered] if covered in domains else []
        candidates[name] = data
        written[name] = os.path.getmtime(path)
    if not candidates and scope != "unchanged":
        fail("no candidates: the finders have not run for this round", 2)

    verdicts = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(meta["paths"]["verdicts"], "*.json"))):
        where = f"verdicts/{os.path.basename(path)}"
        match = VERDICT_FILE.match(os.path.basename(path))
        if not match:
            fail(f"{where}: name must be <finder>.<index>.<verifier>.json")
        finder, index, verifier = match.group(1), int(match.group(2)), match.group(3)
        if finder not in candidates or index >= len(candidates[finder]["candidates"]):
            fail(f"{where}: no such candidate")
        if os.path.getmtime(path) < written[finder]:
            fail(f"{where}: older than candidates/{finder}.json: the finder ran again, verify its candidates again")
        verdict = read(path, "verdicts")
        harness.stamp(path, verdict, ran.get(f"verdicts/{finder}.{index}.{verifier}"), write)
        verdict["verifier"] = verifier
        verdicts[(finder, index)].append(verdict)
    return head, depth, candidates, verdicts


def decide(depth, cast, partial):
    """(outcome, missing reason or None) for one candidate's verdicts."""
    needed = VERDICTS_FOR[depth]
    if depth == "deep" and len(cast) >= 2 and len({v["verdict"] for v in cast[:2]}) > 1:
        needed = 3
    if len(cast) < needed:
        if not partial:
            return None, f"{len(cast)} of {needed} verdict(s)"
        return "cut", f"not verified ({len(cast)} of {needed} verdict(s))"
    keep = sum(1 for v in cast if v["verdict"] == "keep")
    return ("keep" if keep * 2 > len(cast) else "cut"), None


def carry_over(meta, triage, scope):
    """Kept findings of the previous round that still apply without a new verification."""
    if scope not in ("delta", "unchanged"):
        return []
    path = os.path.join(os.path.dirname(meta["paths"]["findings"]), "previous-findings.json")
    if not os.path.isfile(path):
        return []
    previous = read(path, "findings")
    if previous["head"] != triage["round"]["previous_head"]:
        return []
    changed = set((meta.get("delta") or {}).get("files") or []) if scope == "delta" else set()
    out = []
    for index, old in enumerate(previous["findings"]):
        if old["verification"]["outcome"] != "keep" or old["file"] in changed:
            continue
        finding = {k: v for k, v in old.items() if k != "id"}
        finding["carried_from"] = previous["head"]
        finding["verification"] = {
            "verdicts": [{"verifier": "bugbot", "verdict": "keep",
                       "reason": f"carried from round at {previous['head'][:9]} ({old['id']}): "
                                 "the PR did not change this file since"}],
            "outcome": "keep", "duplicate_of": None}
        finding["_key"] = f"carried/{index}"
        finding["_dup"] = None
        out.append(finding)
    return out


def cap_lows(kept, limit):
    """(kept, cut): at most `limit` kept Lows, carried ones first; 0 keeps them all."""
    lows = [f for f in kept if f["severity"] == "Low"]
    if limit <= 0 or len(lows) <= limit:
        return kept, []
    order = sorted(range(len(lows)), key=lambda i: (not lows[i].get("carried_from"), i))
    over = {id(lows[i]) for i in order[limit:]}
    out = []
    for finding in kept:
        if id(finding) in over:
            finding["verification"]["outcome"] = "cut"
            finding["verification"]["verdicts"].append(
                {"verifier": "bugbot", "verdict": "cut",
                 "reason": f"over review.max_lows ({limit}): {len(lows)} Lows kept this round"})
            out.append(finding)
    return [f for f in kept if id(f) not in over], out


def kept_severity(candidate_severity, verdicts):
    """Keep the highest level any independent keep verdict can support."""
    severities = [v["severity"] for v in verdicts
                  if v.get("verdict") == "keep" and v.get("severity") in SEVERITY_RANK]
    return min(severities, key=SEVERITY_RANK.__getitem__) if severities else candidate_severity


def final_verdict(findings):
    kept = [f for f in findings if f["verification"]["outcome"] == "keep"]
    blocking = any(f["severity"] in ("Critical", "High") and not f["pre_existing"] for f in kept)
    review_needed = any(f["severity"] in ("Critical", "High", "Medium") for f in kept)
    return "REQUEST_CHANGES" if blocking else ("COMMENT" if review_needed else "PASS")


def order_findings(kept, cut):
    return sorted(kept + cut,
                  key=lambda f: (SEVERITY_RANK[f["severity"]],
                                 f["verification"]["outcome"] != "keep",
                                 f["pre_existing"], f["file"], f["line"]))


def assemble(args):
    meta = read(args.meta, "meta")
    triage = read(args.triage, "triage")
    scope = triage["round"]["scope"]
    if scope == "capped":
        fail(f"round {triage['round']['number']} is capped (review.max_rounds): nothing to assemble; "
             "triage --full reviews anyway", 2)
    head, depth, candidates, verdicts = load_round(meta, triage, scope)

    kept, cut, missing = [], [], []
    for finder, data in candidates.items():
        for index, candidate in enumerate(data["candidates"]):
            cast = sorted(verdicts.get((finder, index), []), key=lambda v: int(v["verifier"][1:]))
            outcome, gap = decide(depth, cast, args.partial)
            if outcome is None:
                missing.append(f"{finder}/{index} ({gap})")
                continue
            finding = dict(candidate)
            if outcome == "keep":
                keeps = [v for v in cast if v["verdict"] == "keep"]
                finding["severity"] = kept_severity(finding["severity"], keeps)
                if any(v["checks"]["from_pr"] == "pre_existing" for v in keeps):
                    finding["pre_existing"] = True
            verdicts_out = [{"verifier": v["verifier"], "verdict": v["verdict"], "reason": v["reason"]} for v in cast]
            if gap:
                verdicts_out.append({"verifier": "bugbot", "verdict": "cut", "reason": gap})
            duplicate = next((v["duplicate_of"] for v in cast if v.get("duplicate_of")), None)
            finding["verification"] = {"verdicts": verdicts_out, "outcome": outcome, "duplicate_of": None}
            finding["_key"] = f"{finder}/{index}"
            finding["_dup"] = duplicate
            (kept if outcome == "keep" else cut).append(finding)
    if missing:
        fail("verdicts missing for " + ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
             + ": run the verifiers, or --partial to assemble what is there", 2)

    def rank(f):
        return (SEVERITY_RANK[f["severity"]], f["pre_existing"], f["file"], f["line"])

    carried = carry_over(meta, triage, scope)
    kept.extend(carried)
    kept.sort(key=rank)
    seen, final_kept = {}, []
    for finding in kept:
        same = (finding["file"], norm(finding["title"]))
        target = seen.get(same) or next((k for k in final_kept if k["_key"] == finding["_dup"]), None)
        if target is not None:
            finding["verification"]["outcome"] = "cut"
            finding["verification"]["duplicate_of"] = target["_key"]
            cut.append(finding)
            continue
        seen[same] = finding
        final_kept.append(finding)
    final_kept, capped = cap_lows(final_kept, args.max_lows)
    cut.extend(capped)
    cut.sort(key=rank)

    ordered = order_findings(final_kept, cut)
    ids = {f["_key"]: f"F{n}" for n, f in enumerate(ordered, 1)}
    for finding in ordered:
        finding["id"] = ids[finding["_key"]]
        if finding["verification"]["duplicate_of"]:
            finding["verification"]["duplicate_of"] = ids[finding["verification"]["duplicate_of"]]
        del finding["_key"], finding["_dup"]

    blocking = [f for f in final_kept if f["severity"] in ("Critical", "High") and not f["pre_existing"]]
    verdict = final_verdict(final_kept)
    out = {
        "$schema": schema.schema_ref("findings", os.path.dirname(meta["paths"]["findings"])),
        "version": 2,
        "repo": meta["repo"],
        "pr": meta["pr"],
        "head": head,
        "depth": depth,
        "verdict": verdict,
        "note": None,
        "round": {"number": triage["round"]["number"], "scope": scope,
                  "previous_head": triage["round"]["previous_head"], "carried": len(carried)},
        "finders": [{"name": name, "domains": data["domains"]} for name, data in candidates.items()],
        "findings": [{k: f[k] for k in FIELD_ORDER if k in f} for f in ordered],
    }
    try:
        schema.validate(out, "findings")
    except schema.ValidationError as error:
        fail("refusing to write an invalid findings.json:\n" + "\n".join(error.errors[:5]))
    target = meta["paths"]["findings"]
    fd, temp = tempfile.mkstemp(prefix=".findings.", suffix=".json", dir=os.path.dirname(target))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, target)
    print("\t".join([verdict, str(len(final_kept)), str(len(cut)), str(len(blocking)), target]))


FIELD_ORDER = ("id", "domain", "finder", "severity", "pre_existing", "category", "title", "explanation",
               "suggestion", "file", "line", "side", "evidence", "rule", "confidence", "carried_from",
               "verification")


def rows(args):
    """kind<TAB>id<TAB>severity<TAB>pre<TAB>confidence<TAB>file:line<TAB>title<TAB>verdicts<TAB>detail"""
    data = read(args.file, "findings")
    print("\t".join(["verdict", data["verdict"], data["depth"], data["head"][:9]]))
    for f in data["findings"]:
        v = f["verification"]
        tally = " ".join(f"{x['verifier']}:{x['verdict']}" for x in v["verdicts"])
        detail = (f"duplicate of {v['duplicate_of']}" if v.get("duplicate_of")
                  else next((x["reason"] for x in v["verdicts"] if x["verdict"] == "cut"), ""))
        print("\t".join([v["outcome"], f["id"], f["severity"], "1" if f["pre_existing"] else "0",
                         f["confidence"], f"{f['file']}:{f['line']}", f["title"].replace("\t", " "),
                         tally, detail.replace("\t", " ").replace("\n", " ")]))


def main():
    parser = argparse.ArgumentParser(prog="findings.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("assemble")
    sub.add_argument("--meta", required=True)
    sub.add_argument("--triage", required=True)
    sub.add_argument("--partial", action="store_true")
    sub.add_argument("--max-lows", type=int, default=0, help="most kept Lows; 0: no limit")
    sub.set_defaults(handler=assemble)
    sub = commands.add_parser("rows")
    sub.add_argument("--file", required=True)
    sub.set_defaults(handler=rows)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
