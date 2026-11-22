# Finder rules

Shared by every finder (`finder-bugs`, `finder-security`, `finder-convention`), on top of
`review-rules.md`. Each finder reads both first; its own file only says what to look for. Keep the
finding process here, so the finders cannot drift apart.

You look for problems the change introduces, with one focus, over part of one pull request. You
write what you find as candidates; other agents verify each one independently afterwards, and only
what survives reaches a person. So report what you can back with evidence, and nothing else: a
candidate that will not survive verification costs a verifier run and teaches nobody anything.

## Input (in the dispatch prompt)

- `home` — absolute path of the bot's home; `.agents/`, `schemas/` and `bin/bugbot` are under it.
- `finder` — the name of this run (for example `bugs-components`): also your file name.
- `domains` — the domains you cover and their files (from `triage.json` `files[]`, with `risk`,
  `test`, `sensitive`). Empty: the whole diff.
- `meta` — path of `meta.json`: `head.sha`, `merge_base`, `paths.diff`, `paths.worktree`,
  `paths.clone`, `paths.candidates`, `knowledge`.
- `depth` — the recorded review depth.
- `scope` — `full` or `delta` (from `triage.json` `round.scope`). On `delta`, review only the files in
  `meta.delta.files`, reading their hunks in `meta.delta.patch`: the rest of the PR was reviewed in
  an earlier round and its findings are carried over. Whole files and callers are still read for
  context, as always.

## Ground rules

Read `.agents/skills/bugbot/references/review-rules.md` first: where to read code and instructions, safety, severity
and validation are there. On top of it:

- **Stay in your lane**: your domains and your focus. Other finders cover the rest.

## What not to report

- What `review-rules.md` says is not a problem (style, what CI enforces). When a repository
  instruction makes it a rule, only `finder-convention` reports it, with the rule quoted.
- Problems in code the PR did not change, unless the change makes them reachable or worse; then
  set `pre_existing: true`.
- Guesses: anything you would have to run the code to know. If it is likely but reading cannot
  settle it, report it with `confidence: PLAUSIBLE` only when it could be `Critical` or `High`. Reading
  settles it (`CONFIRMED`) when every step of the claim is code you quoted from this repository;
  it does not when a step depends on runtime or library behavior you described but did not quote.
- The same problem twice. When one cause shows up in several places, report it once, at the first
  occurrence, and list the other places in `explanation`: the author fixes the class, not a line.
  So when you find one, `grep` your files and the diff for the same pattern before writing it, and
  add each other place you confirmed to `evidence`.

## Each candidate

Shape: the `candidate` in `schemas/candidates.schema.json`. The fields that need judgment:

- `severity`: `Critical`, `High`, `Medium` or `Low`, as `review-rules.md` defines them. Nothing else.
- `title`: the problem in one line, not the fix. Keep it stable: the same problem on the next push
  must get the same title.
- `explanation`: what happens at runtime and why, in two to four sentences.
- `file`, `line`, `side`: where a comment belongs. `RIGHT` and a line of the new file for added or
  kept code; `LEFT` and a line of the old file for removed code.
- `evidence`: at least one entry, the code quoted **verbatim** from the file with its line; add the
  file:line of everything else the claim rests on (the caller, the rule, the test). A claim inferred
  from a name is not evidence: open the definition.
  - `ref: base` for a line from before the PR (code it removed or changed); `ref: head` (default)
    for the worktree.
  - Only files of this repository or, with `repo`, of another repository of the workspace. Behavior
    of a dependency or the platform (framework internals, `node_modules`, SDKs) goes in
    `explanation`, named precisely; it is not evidence a verifier can open, so a claim that rests
    mainly on it is `PLAUSIBLE`.
- `rule`: for `category: convention`, the source (`REVIEW.md#Section` or `AGENTS.md:42`) and the
  sentence quoted verbatim.
  - `repo` for a line in another repository of the workspace (see `review-rules.md`), for example
    the code a documentation page describes; it is checkable like any other line.
- `suggestion`: only exact replacement code for the anchored lines, when it is short and certain.

## Output

Write `<paths.candidates>/<finder>.json`, shaped `{"version": 2, "candidates": [...]}`, and validate it
(`review-rules.md`). It holds the `candidates` list and nothing about you: the file name says which finder you are, and the CLI takes
each file's domain from `triage.json`. An empty list is a valid answer. Reply with the file path and
the number of candidates, unless the dispatch asks for more.
