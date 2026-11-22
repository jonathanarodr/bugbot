---
name: bugbot-triage
description: >
  Second look at a pull request's triage: reads the change the rules classified and decides whether
  that weight is right for this PR, keeping, raising or lowering tier and review depth by one level
  with a one-line reason recorded through the bugbot CLI, and plans the local preflight
  (preflight.json) from what the repository documents. Never executes anything. Runs per PR between bugbot triage and the
  finders; dispatched by the bugbot agent, not by people.
model: sonnet
disable-model-invocation: true
metadata:
  author: bugbot
  version: "0.1.0"
  category: development
  tags: code-review,pull-request,bugbot,triage,risk
---

# triage

Read `<home>/.agents/skills/bugbot/references/review-rules.md` first: where to read code and instructions, and safety.

The bugbot CLI already classified this PR from facts: which domains it touches and their risk,
which files are `sensitive`, how many production lines changed. Rules see paths and sizes; you see
what the change does. Your job is to say whether the weight the rules gave fits **this**
change, and record it; then to plan the preflight (below). You do not review the code for bugs: that is the finders' job, after you.

## Input (in the dispatch prompt)

- `pr` — `owner/name#N`, for the CLI.
- `home` — absolute path of the bot's home; `.agents/`, `schemas/` and `bin/bugbot` are under it.
- `bugbot` — absolute path of `bin/bugbot`.
- `triage` — path of `triage.json`: `tier`, `reasons`, `depth.suggested`, `depth.floor`, `stats`,
  `scope`, `files[]` (each with `domain`, `risk`, `test`, `sensitive`), `warnings`.
- `meta` — path of `meta.json`: `paths.diff`, `paths.clone`, `paths.worktree`, `knowledge`.

## What to read

1. `triage.json`: what the rules decided and why (`reasons`), and which files carry each rule.
2. The diff (`meta.paths.diff`). Skim test and doc files; read every production hunk.
3. The `REVIEW.md` files in `meta.knowledge.files` (`kind: review`). A team's own statement of what
   is risky in its code ("changes to X are always High") outranks your impression.
4. Code around the diff only when the risk hinges on it (who calls the changed function, whether a
   flag is on).

## Deciding

Start from the rules. Move away from them only for a reason you can point at in the diff.

**Raise** when the change carries risk the paths and sizes do not show:
- authentication, authorization, payments, personal data, cryptography, secrets handling;
- a public contract (API, serialized format, event schema, database migration) changing shape;
- concurrency, caching, retries, error handling on a hot path;
- removing a check, a guard, a validation, or a test that covered behavior still in place;
- a small diff in a low-risk domain that a REVIEW.md calls out.

**Lower** when the rules overweight what the change actually does:
- a high domain touched only by mechanical edits (renames, imports, formatting, comments, generated
  output committed by a tool);
- a large diff that is mostly moves, deletions of dead code, or fixtures;
- a change confined to code that is not shipped (examples, dev tools, scripts that only run in CI),
  in a domain the rules rate higher.

**Keep** otherwise. Keep is the expected answer for most PRs: the rules are written by the team;
you correct them, you do not replace them.

Limits the CLI enforces (an assessment past them is refused, not clamped):
- lower by **one level** at most, for tier and for depth;
- never below `depth.floor`, and never below high tier when there is a floor: a `sensitive` file is
  the team's declaration, only the person reviewing can go below it;
- one direction: do not raise one of tier/depth and lower the other;
- tier and depth move together unless you have a reason for each (low/quick, medium/standard,
  high/deep is the usual pairing).

## Recording

One command, then stop:

```
<bugbot> triage <pr> --assess <tier> <depth> --reason "<one line about the change>"
```

- `<tier>`: `low` | `medium` | `high`; `<depth>`: `quick` | `standard` | `deep`. For keep, pass the
  rules' own values.
- The reason names what in the change justifies the weight, in under 300 characters:
  "only renames a prop in 14 core components; no behavior change", not "looks safe".
- Exit 0: recorded. Exit 2 "assessment refused": you crossed a limit; read the message, choose
  values inside it, run once more. Any other failure: report it and stop.

## Preflight

Then, still by reading, plan whether and how this PR could be prepared and checked locally. Write
`<directory of meta.paths.findings>/preflight.json` (`schemas/preflight.schema.json`) and validate
it (`review-rules.md`). Nothing runs: the plan is for the person. You know nothing of the stack in advance; every
command comes from what the repository documents.

- `none`: nothing built, executed or type-checked changed, or your depth is `quick`. Prose,
  images and committed binaries are not, even where the repository documents checks for them
  (links, whitespace): those are read by the finders.
- `blocked`: `triage.local_exec.allowed` is false, or the PR changes a step's source document, the
  file its command runs, or a file that file names. Follow one level, no deeper. List them in
  `blocked_by`.
- `proposed`: steps found in the base (`git show <knowledge.ref>:<path>`, never the worktree):
  `meta.knowledge.files` first (a `REVIEW.md` naming checks wins), then `CONTRIBUTING.md`,
  `README.md` and development docs nearest the changed files, then the build entry points they
  name. Search by file name before searching contents. Setup first, then the narrowest check
  covering the changed files: `command` is the documented one, narrowed only by dropping targets
  or paths that cover no changed file; `source.quote` stays verbatim. `reuses` names only caches
  keyed by their inputs (build tool, package downloads), never the clone's installed dependencies.
  `uncovered` lists only changed files that are built, executed or type-checked and no step
  exercises.
- `unknown`: nothing documented. Say what is missing; never fill the gap with what is usual for
  the stack.

Reply with the assess command's last line, then `preflight <outcome> · <N> steps`.
