#!/usr/bin/env python3
"""bugbot publish: build the GitHub review for one PR round from findings.json. No network here:
the shell command reads the previous bot review and posts; this module only builds, so it runs
offline against a findings file.

What gets posted, one review per round, as the configured reviewer (review.author):

  body       status line, numbers (open findings by severity first), summary, scope, Outside the
             diff, Audit (cut findings), footer,
             then the ledger and the marker (HTML comments, invisible)
  comments   one inline comment per anchored finding that has no thread yet

Rules carried over from the first prototype, adapted:
  * Anchors are validated against the diff hunks: GitHub rejects the whole review when one inline
    comment points outside a hunk. A finding that cannot be anchored goes to "Outside the diff".
  * Fingerprint = sha1(file + English title)[:12], the line excluded (it moves on every rebase).
    It is written in every inline comment (<!-- bugbot:f=... -->) and in the ledger.
  * Ledger (<!-- bugbot:ledger {...} -->) in the review body: every finding the PR has carried,
    with status open/fixed, the head it was first seen at and last confirmed at.
  * No repeats: a finding whose fingerprint already has a thread (from the previous ledger or from
    an existing inline comment) is not commented again; it stays open in the ledger.
  * Fixed: a previous open finding missing from this round is fixed only when its file changed in
    the PR since the previous round (meta.delta / changed files); a reviewer that merely did not
    repeat itself never closes a finding nobody addressed.
  * REQUEST_CHANGES is used for a kept, new Critical/High; COMMENT for Medium or non-blocking
    pre-existing Critical/High; PASS is APPROVE, with Low findings retained as comments.
  * Language: when review.language is not English, the posted text comes from translate.json
    (schemas/translate.schema.json), written by the bot agent from findings.json. Fingerprints
    always use the English title, so a language change never reposts a finding.

  build    print the review payload (JSON) and a TSV summary on stderr

Standard library only, compatible with the macOS system Python (3.9).
"""

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))
import schema  # noqa: E402

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
LEDGER_OPEN = "<!-- bugbot:ledger"
FP_MARK = re.compile(r"<!--\s*bugbot:f=([0-9a-f,]+)\s*-->")

STATUS = {
    "REQUEST_CHANGES": "🔴 Request changes",
    "COMMENT": "🟡 Comment",
    "PASS": "🟢 Approved (LGTM)",
    "SKIPPED": "⚪ Not reviewed",
    "INVALID_INVOCATION": "⚪ Not reviewed",
}
SEVERITY_MARK = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🔵"}

# The English text of the review body; translate.json may replace any of these keys.
TEXT = {
    "heading": "Bugbot",
    "pass_tldr": "Nothing to fix.",
    "unchanged_tldr": "Nothing new to review: the PR did not change since the last review.",
    "findings_line": "**Findings:** {open} open ({critical} Critical, {high} High, {medium} Medium, {low} Low) · {new} new · {carried} already under discussion · {fixed} fixed since {since}",
    "findings_line_first": "**Findings:** {open} open ({critical} Critical, {high} High, {medium} Medium, {low} Low)",
    "scope_line": "**Review:** round {round} · {scope} · depth {depth} · {files} file(s), {lines} line(s)",
    "pre_existing": "pre-existing",
    "plausible": "Likely, but not confirmed by reading the code alone.",
    "rule": "Rule",
    "outside_title": "Outside the diff — {n} finding(s) on lines this PR does not touch",
    "outside_note": "GitHub only accepts inline comments on lines the diff touches.",
    "audit_title": "Audit — {total} candidate(s), {kept} kept, {cut} cut",
    "footer": "bugbot reviewed `{head}` against `{base}` · round `{round}`",
    "audit_header": "| # | Severity | Finding | Outcome |",
    "duplicate_of": "duplicate of {id}",
}

# Built-in text per review.language, only for what people read in the discussion: status, summary
# lines, inline comment labels. The audit table and the bot's internal values (outcome, verdict,
# scope, depth) are bot logic and stay in English. translate.json "text" still overrides any key.
# A language not listed here posts this text in English.
LOCALES = {
    "pt-BR": {
        "status": {
            "REQUEST_CHANGES": "🔴 Solicitar alterações",
            "COMMENT": "🟡 Comentário",
            "PASS": "🟢 Aprovado (LGTM)",
            "SKIPPED": "⚪ Não revisado",
            "INVALID_INVOCATION": "⚪ Não revisado",
        },
        "text": {
            "pass_tldr": "Nada a corrigir.",
            "unchanged_tldr": "Nada novo para revisar: o PR não mudou desde a última revisão.",
            "findings_line": "**Achados:** {open} em aberto ({critical} Critical, {high} High, {medium} Medium, {low} Low) · {new} novo(s) · {carried} já em discussão · {fixed} corrigido(s) desde {since}",
            "findings_line_first": "**Achados:** {open} em aberto ({critical} Critical, {high} High, {medium} Medium, {low} Low)",
            "scope_line": "**Revisão:** rodada {round} · {scope} · profundidade {depth} · {files} arquivo(s), {lines} linha(s)",
            "pre_existing": "pré-existente",
            "plausible": "Provável, mas não confirmado só pela leitura do código.",
            "rule": "Regra",
            "outside_title": "Fora do diff — {n} achado(s) em linhas que este PR não altera",
            "outside_note": "O GitHub só aceita comentários inline em linhas que o diff altera.",
            "footer": "bugbot reviewed `{head}` against `{base}` · round `{round}`",
        },
        "labels": {
            "correctness": "corretude", "security": "segurança", "performance": "desempenho",
            "api-contract": "contrato de API", "accessibility": "acessibilidade", "convention": "convenção",
            "test": "teste", "docs": "documentação",
        },
    },
}
LABELS = {}


def label(value):
    return LABELS.get(value, value)


def fingerprint(finding):
    title = " ".join((finding.get("title") or "").lower().split())
    return hashlib.sha1((finding.get("file", "") + "\0" + title).encode("utf-8")).hexdigest()[:12]


def commentable(diff_text):
    """{(path, side): set(lines)} GitHub accepts as an anchor: RIGHT = added or context lines of the
    new file, LEFT = removed or context lines of the old file."""
    out, path, old, new = {}, None, 0, 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:].strip() if raw.startswith("+++ b/") else None
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        header = HUNK.match(raw)
        if header:
            old, new = int(header.group(1)), int(header.group(3))
            continue
        if path is None:
            continue
        right = out.setdefault((path, "RIGHT"), set())
        left = out.setdefault((path, "LEFT"), set())
        if raw.startswith("+"):
            right.add(new); new += 1
        elif raw.startswith("-"):
            left.add(old); old += 1
        elif raw.startswith(" "):
            right.add(new); left.add(old); new += 1; old += 1
    return out


def read_ledger(body):
    if not body or LEDGER_OPEN not in body:
        return {}
    block = body.split(LEDGER_OPEN, 1)[1].split("-->", 1)[0]
    try:
        entries = json.loads(block).get("findings", [])
        legacy = {"Important": "High", "Nit": "Low"}
        for entry in entries:
            entry["severity"] = legacy.get(entry.get("severity"), entry.get("severity"))
        return {e["fp"]: e for e in entries}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def render_ledger(entries):
    payload = {"v": 1, "findings": sorted(entries, key=lambda e: (e["status"], e["fp"]))}
    return LEDGER_OPEN + "\n" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n-->"


def tr(translation, finding_id, field, default):
    return ((translation.get("findings") or {}).get(finding_id) or {}).get(field) or default


def comment_text(f, translation, text):
    title = tr(translation, f["id"], "title", f["title"])
    explanation = tr(translation, f["id"], "explanation", f["explanation"])
    tags = [f["severity"], label(f["category"])] + ([text["pre_existing"]] if f.get("pre_existing") else [])
    out = f"{SEVERITY_MARK.get(f['severity'], '🟡')} **{' · '.join(tags)}** — {title}\n\n{explanation.strip()}"
    if f.get("suggestion"):
        out += "\n\n```suggestion\n" + f["suggestion"].rstrip("\n") + "\n```"
    if f.get("rule"):
        out += f"\n\n<sub>{text['rule']}: {f['rule']['source']} — “{f['rule']['quote'].strip()}”</sub>"
    if f.get("confidence") == "PLAUSIBLE":
        out += f"\n\n<sub>{text['plausible']}</sub>"
    return out + f"\n<!-- bugbot:f={fingerprint(f)} -->"


def review_event(verdict, looked):
    if not looked:
        return "COMMENT"
    return {"REQUEST_CHANGES": "REQUEST_CHANGES", "COMMENT": "COMMENT", "PASS": "APPROVE"}.get(
        verdict, "COMMENT")


def build(args):
    findings = schema.read_document(args.findings, "findings")
    meta = schema.read_document(args.meta, "meta")
    if findings["head"] != meta["head"]["sha"]:
        sys.exit(f"publish: findings.json is for {findings['head'][:9]}, the PR is at {meta['head']['sha'][:9]}: "
                 "assemble this round first")
    diff_text = open(meta["paths"]["diff"], encoding="utf-8", errors="replace").read()
    translation = schema.read_document(args.translation, "translate") if args.translation else {}
    if translation and translation["head"] != findings["head"]:
        sys.exit(f"publish: translate.json is for {translation['head'][:9]}, findings.json for {findings['head'][:9]}")
    locale = LOCALES.get(args.language) or {}
    LABELS.update(locale.get("labels") or {})
    text = dict(TEXT, **(locale.get("text") or {}), **(translation.get("text") or {}))
    status_text = dict(STATUS, **(locale.get("status") or {}))
    previous = read_ledger(open(args.previous_body, encoding="utf-8").read() if args.previous_body else "")
    threads = set()
    if args.threads:
        for line in open(args.threads, encoding="utf-8").read().split():
            threads.update(fp for fp in line.split(",") if fp)

    head = findings["head"]
    rnd = findings.get("round") or {"number": 1, "scope": "full", "previous_head": None, "carried": 0}
    delta = meta.get("delta") or {}
    changed = set(delta.get("files") or []) if delta.get("from_head") else None

    kept = [f for f in findings["findings"] if f["verification"]["outcome"] == "keep"]
    cut = [f for f in findings["findings"] if f["verification"]["outcome"] != "keep"]
    anchors = commentable(diff_text)

    ledger, seen, inline_by_anchor, outside = [], set(), {}, []
    new_count = carried_count = 0
    for f in kept:
        fp = fingerprint(f)
        seen.add(fp)
        known = previous.get(fp)
        ledger.append({"fp": fp, "path": f["file"], "line": f["line"], "severity": f["severity"],
                       "category": f["category"], "title": f["title"], "status": "open",
                       "first_sha": (known or {}).get("first_sha") or head, "last_confirmed": head})
        has_thread = fp in threads or (known is not None and known.get("status") == "open")
        if has_thread:
            carried_count += 1
            continue
        new_count += 1
        key = (f["file"], f["side"])
        if f["line"] in anchors.get(key, set()):
            inline_by_anchor.setdefault((f["file"], f["line"], f["side"]), []).append(f)
        else:
            outside.append(f)

    fixed = 0
    for fp, known in previous.items():
        if fp in seen:
            continue
        entry = dict(known)
        if known.get("status") == "open" and changed is not None and known.get("path") in changed:
            entry["status"] = "fixed"
            entry["fixed_at"] = head
            fixed += 1
        ledger.append(entry)

    comments = []
    for (path, line, side), group in sorted(inline_by_anchor.items()):
        comments.append({"path": path, "line": line, "side": side,
                         "body": "\n\n---\n\n".join(comment_text(f, translation, text) for f in group)})

    verdict = findings["verdict"]
    # A round that looked at nothing new (scope unchanged) says so, whatever was carried: the marker
    # then reads SKIPPED, which is what lets the next such round collapse this one.
    looked = rnd["scope"] != "unchanged"
    marker_verdict = verdict if looked else "SKIPPED"
    if marker_verdict == "PASS" and any(f["severity"] == "Low" for f in kept):
        status = "🟢 Approved with comments" if args.language.startswith("en") else "🟢 Aprovado com comentários"
    else:
        status = status_text.get(marker_verdict, marker_verdict)
    still_open = [e for e in ledger if e["status"] == "open"]
    counts = {"open": len(still_open)}
    counts.update({severity.lower(): sum(1 for e in still_open if e["severity"] == severity)
                   for severity in ("Critical", "High", "Medium", "Low")})

    body = [f"## {text['heading']}", "", f"#### {status}", ""]
    if rnd.get("previous_head"):
        body.append(text["findings_line"].format(**counts, new=new_count, carried=carried_count,
                                                 fixed=fixed, since=f"`{rnd['previous_head'][:9]}`"))
    else:
        body.append(text["findings_line_first"].format(**counts))
    body.append("")
    tldr = translation.get("tldr") or args.tldr or ""
    if not kept and rnd["scope"] == "unchanged":
        tldr = tldr or text["unchanged_tldr"]
    elif not kept:
        tldr = tldr or text["pass_tldr"]
    elif all(f["severity"] == "Low" for f in kept):
        tldr = tldr or ("No blocking findings; approving with low-priority comments."
                        if args.language.startswith("en") else
                        "Não há achados bloqueantes; o PR será aprovado com comentários de baixa prioridade.")
    if tldr:
        body += [tldr.strip(), ""]
    scope_files = len(delta.get("files") or []) if rnd["scope"] == "delta" else meta["stats"]["files"]
    scope_lines = delta.get("lines", 0) if rnd["scope"] == "delta" else meta["stats"]["additions"] + meta["stats"]["deletions"]
    body.append(text["scope_line"].format(round=rnd["number"], scope=label(rnd["scope"]), depth=label(findings["depth"]),
                                          files=scope_files, lines=scope_lines))

    if outside:
        rows = "\n".join(
            f"- {SEVERITY_MARK.get(f['severity'], '🟡')} **{label(f['severity'])}** · `{f['file']}:{f['line']}` — "
            f"{tr(translation, f['id'], 'title', f['title'])}<!-- bugbot:f={fingerprint(f)} -->"
            for f in outside)
        body += ["", f"<details><summary>{text['outside_title'].format(n=len(outside))}</summary>", "", rows, "",
                 f"{text['outside_note']}", "</details>"]

    total = len(kept) + len(cut)
    if total:
        rows = [text["audit_header"], "|---|---|---|---|"]
        for f in findings["findings"]:
            reason = next((v["reason"] for v in f["verification"]["verdicts"] if v["verdict"] == "cut"), "")
            if f["verification"].get("duplicate_of"):
                reason = text["duplicate_of"].format(id=f["verification"]["duplicate_of"])
            verdict_cell = label(f["verification"]["outcome"].upper()) + (f" — {reason}" if reason and f in cut else "")
            title = f["title"].replace("|", "\\|")
            rows.append(f"| {f['id']} | {f['severity']} | `{f['file']}:{f['line']}` — {title} | "
                        f"{verdict_cell.replace('|', chr(92) + '|')} |")
        body += ["", f"<details><summary>{text['audit_title'].format(total=total, kept=len(kept), cut=len(cut))}"
                 "</summary>", "", *rows, "", "</details>"]

    body += ["", "<sub>" + text["footer"].format(head=head[:9], base=meta["base"]["branch"], round=rnd["number"])
             + "</sub>", render_ledger(ledger),
             rounds_marker(args.marker_version, head, rnd["number"], marker_verdict)]
    event = review_event(marker_verdict, looked)
    payload = {"commit_id": head, "event": event, "body": "\n".join(body)}
    if comments:
        payload["comments"] = comments
    json.dump(payload, sys.stdout, ensure_ascii=False)
    print("\t".join(map(str, [marker_verdict, len(comments), len(outside), carried_count, fixed, len(cut),
                              "1" if looked else "0"])), file=sys.stderr)


def rounds_marker(version, sha, number, verdict):
    return f"<!-- bugbot:v{version} sha={sha} round={number} verdict={verdict} -->"


def main():
    parser = argparse.ArgumentParser(prog="publish.py")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("build")
    sub.add_argument("--findings", required=True)
    sub.add_argument("--meta", required=True)
    sub.add_argument("--marker-version", type=int, required=True)
    sub.add_argument("--previous-body", default="")
    sub.add_argument("--threads", default="", help="fingerprints already carried by inline comments, one line per comment")
    sub.add_argument("--translation", default="")
    sub.add_argument("--language", default="en", help="review.language: picks the built-in chrome in LOCALES")
    sub.add_argument("--tldr", default="")
    sub.set_defaults(handler=build)
    args = parser.parse_args()
    try:
        args.handler(args)
    except schema.ValidationError as error:
        sys.exit("publish: " + "; ".join(error.errors[:3]))


if __name__ == "__main__":
    main()
