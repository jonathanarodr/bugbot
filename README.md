# Bugbot

Local CLI that prepares pull request reviews: it finds the PRs waiting for your review, brings the diff to your machine and classifies each one by risk before any model runs. Deterministic (zsh + Python stdlib, no LLM); you are the one who reviews, comments and approves.

This document has two parts. **Getting started** and **Commands** are for people using the bot. The rest is the construction guide: decisions made, rules every new piece of code follows, the format of each file and what is left to do. Update it with every change.

## Goal

Support PR review across several GitHub repositories, of one organization, several, or your own account, on any stack, with the reviewer always in the loop. The bot prepares, analyzes and proposes; the person decides, comments and approves, with their own `gh` user.

Orchestration belongs to the `.agents/bugbot.md` agent, which calls sub-agents per stage: triage, review per domain and verification (e.g. a PR touching the `components` domain dispatches that domain's reviewer). The CLI in this directory is the deterministic part those agents use: scripts, no LLM.

The bot knows no organization or repository: the org is an optional value in `bugbot.json` (`github.org`, written by `bugbot init`; left out, `setup` offers clones of any owner), and everything specific to a project lives as a **value** in `repos/<owner>/<name>.json`, with the same keys for every repo. No code, schema, agent or example in this directory names a real org or repository; names such as `acme/web` are fictional.

Inspiration: [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/). We adopted the shape (layers, tiers, voting, convergence), not full automation.

## Getting started

**1. Requirements.** macOS with `zsh`, `git`, `python3` ≥ 3.9 (the system one is fine) and [`gh`](https://cli.github.com) logged in (with SSO authorized for any organization whose repositories you review):
```
gh auth login            # once
gh auth status           # check account and scopes
```
Nothing to install. If `GH_TOKEN` or `GITHUB_TOKEN` is in the environment, it overrides the `gh` login; `doctor` warns about it.

**2. Tell your shell where the bot is.** Clone it anywhere; nothing in the bot assumes a folder. In `~/.zshrc`, with the path of your clone:
```
export BUGBOT_HOME=~/path/to/bugbot      # where you cloned it
export PATH="$BUGBOT_HOME/bin:$PATH"     # to call just `bugbot`
```
The CLI always works out its own location (symlinks included), so `bin/bugbot` runs from any clone even without these lines. The agents and skills are what need `BUGBOT_HOME`: they find the CLI at `$BUGBOT_HOME/bin/bugbot` (or, failing that, `bugbot` on `PATH`) and stop and ask when neither exists. `bugbot doctor` warns when `BUGBOT_HOME` is missing and fails when it points at another copy.

**3. Create the configuration.**
```
bugbot init              # writes bugbot.json with the default values; edit whatever you want afterwards
bugbot init --org <org>  # same, but setup only offers clones of that organization (or user)
```
Without `--org`, `setup` offers the clones of every GitHub owner on the machine (your personal repositories, several organizations) and you pick in the selector. With it, only that owner's.
For overrides of your own, create `bugbot.local.json` with only the keys that change (format in **Schemas → settings**).

**4. Check the environment.**
```
bugbot doctor            # read-only: tools, login, settings, schemas, configs
```

**5. Map your clones.** The bot clones nothing: it uses the clones you already have.
```
bugbot setup             # scans ~, shows the GitHub repos (of github.org, when set) and lets you pick one clone per repo
```
Writes `workspace.json` (your paths) and creates a minimal `repos/<owner>/<name>.json` for each chosen repo that has no config yet. Running it again updates paths and never touches a repo config that already exists.

**6. Calibrate each repo.** The file `setup` creates has no rules: every PR becomes medium and the card says so. Add, at your own pace, the groups that make sense (format and examples in **Schemas → repo**):
- `domains`: the areas of the repo, with `risk` (low/medium/high). This is what changes triage the most;
- `sensitive`: files that always need full attention (build, CI, permissions, public contracts);
- `tests` and `ignore`: tests and generated code;
- `hooks.reviewers.max`: how many reviews are enough for the PR to leave your queue.

After editing: `bugbot format` (code style) and `bugbot doctor --only repos` (validates and checks each glob against the clone).

**7. Daily use.**
```
bugbot inbox             # PRs waiting for your review, filtered by the hooks; pick and go on to fetch + triage
bugbot triage <pr>       # card of a PR already fetched: tier, depth, scope, CI
bugbot clean             # cleans what belongs to PRs already merged
```

**What is yours and what is the bot's**

| File | Owner | Versioned | Written by |
|---|---|---|---|
| `bin/`, `lib/`, `schemas/`, `README.md`, `.editorconfig` | bot | yes | whoever builds the bot |
| `bugbot.json` | you | no | `bugbot init` (org, hooks, limits); you adjust it |
| `bugbot.local.json` | you | no | your overrides of any `bugbot.json` key |
| `workspace.json` | you | no | `bugbot setup` (your paths and login) |
| `repos/<owner>/<name>.json` | you | no | `setup` creates a minimal one; you calibrate it |
| `.bugbot/` | bot | no | cache, worktrees, logs and locks; disposable (`clean`, `purge`) |
| `retro/` | you | no | permanent record of your review decisions (third-party PR data) |

`repos/` stays out of version control because calibration is the reviewer's choice. To start from someone else's calibration, copy their file to `repos/<owner>/<name>.json` and run `bugbot doctor --only repos`.

## Commands

Every command has `--help` and `--no-color`; the ones that write have `--plan` (show without doing) and ask for confirmation, with "no" as the default. UI goes to stderr and data to stdout (`--json`). Exit: `0` ok, `1` failure or cancelled, `2` usage or invalid configuration, `130` Ctrl-C.

| Command | What it does | Main options |
|---|---|---|
| `init` | Creates `bugbot.json` with the initial value of every setting (`x-init` in the settings schema); the org is optional, from `--org` or `BUGBOT_ORG`, never from code (without it, `setup` offers every owner). Does not overwrite without `--force` | `--org`, `--force`, `--print` |
| `setup` | Scans the machine for GitHub clones (only of `github.org` when it is set), picks one canonical clone per repo, writes `workspace.json` and creates the missing `repos/*.json` | `--root`, `--depth`, `--all`, `--yes`, `--remote`, `--plan`, `--json` |
| `inbox` | Lists PRs whose review was requested from you (directly or through a team) in the workspace repos, and, with `github.app`, the open PRs of the App's repositories; already filtered by the hooks; the chosen ones go through `fetch` and `triage` | `--list`, `--hidden`, `--json`, `--query` |
| `fetch <pr>` | Brings the PR to the machine: `diff.patch`, `files.txt`, `meta.json` in the cache; refs under `refs/bugbot/` in the clone (the clone's branch and files do not change) | `--repo`, `--worktree`, `--plan`, `--json` |
| `triage <pr>` | Computes tier, suggested depth, scope and the round scope (full, delta, unchanged, capped) from the files and the PR state; runs the hooks; shows the card and records your decision per sha, pinning the reviewed head for the next round's delta. `--assess` records the `triage` agent's second opinion | `--repo`, `--depth`, `--no-confirm`, `--force`, `--full`, `--assess <tier> <depth> --reason`, `--json` |
| `validate <file>` | Validates a JSON file an agent wrote against its schema, inferred from where the file is (`candidates/`, `verdicts/`, `<name>.json`). It is what the agents run before replying | `--schema` |
| `findings <pr>` | Assembles the round's `findings.json`: majority, highest confirmed severity, duplicates, Low limit and order by severity. Publishes nothing | `--repo`, `--partial`, `--show`, `--all`, `--json` |
| `publish <pr>` | Publishes a review as `review.author`: shows it first and only sends it on your yes. The event is `REQUEST_CHANGES`, `COMMENT` or `APPROVE`, from the final verdict | `--repo`, `--dry-run`, `--yes`, `--tldr`, `--translation`, `--json` |
| `retro record <pr>` | Validates and appends the `bugbot-retro` skill's report to the PR's permanent record; used automatically after a published `APPROVE` when enabled | `--repo`, `--trigger auto_approve\|manual` |
| `clean [<pr>]` | Without a PR: removes what belongs to merged PRs. With a PR: removes that one, in any state. Never touches `retro/` | `--repo`, `--plan`, `--yes` |
| `purge` | Deletes everything the bot created (cache, worktrees, refs, logs). `retro/` only with `--retro` and its own confirmation | `--retro`, `--plan`, `--yes`, `--yes-retro` |
| `config` | Shows every setting in use, where it came from (environment, `.local`, `bugbot.json`) and what it controls | `--json` (for agents) |
| `doctor` | Read-only health check: formatting, environment, settings, schemas, workspace, repo configs, cache and this README | `--only`, `--quick`, `--offline`, `--json` |
| `format` | Rewrites the bot's JSON files in the `.editorconfig` code style | `--check` |

`<pr>` accepts the URL, `owner/name#N` or the number with `--repo owner/name`.

Path variables (environment only): `BUGBOT_HOME` (where the bot is cloned, read by the agents; the CLI derives its own), `BUGBOT_WORKSPACE_FILE` (default `workspace.json`), `BUGBOT_DATA_DIR` (default `.bugbot`, disposable), `BUGBOT_RETRO_DIR` (default `retro`, permanent). Every `bugbot.json` key also has its own `BUGBOT_*` variable (listed in **Schemas → settings** and in `bugbot config`).

## Review rounds

A **round** is a bot review published on the PR. The count comes **from GitHub, not from local state**: it survives `clean`, another machine and another person running the bot. It follows the Claude Code team ("review has to converge": "a round cap", "diff-only re-review after the first few passes", "no repeats").

**Marker.** Every bot review carries in its body:
```
<!-- bugbot:v1 sha=<reviewed head> round=<n> verdict=<verdict> -->
```
`fetch` lists the PR's reviews, keeps those with the current version's marker **and** whose author is `review.author`, and reads the last one: the head it covered and the round number. The author check is what makes the marker trustworthy: anyone can paste the marker into a comment, but nobody can post as that author. A marker in someone else's review is ignored and listed in `meta.rounds.ignored_foreign`. `review.author` is `@me` (the gh user, default) or a bot login (`my-app[bot]`), for when publishing moves to a GitHub App.

**The delta is the PR compared with itself**, not the new commits. Comparing commits does not work: a merge of the default branch brings everyone's work, and a rebase changes every sha. `fetch` compares the PR's diff at the head of the bot's last review (`merge_base₁ → head₁`) with the diff now (`merge_base₂ → head₂`), file by file, ignoring hunk headers and context. So nothing the author changed is left out between rounds, and nothing that came from the base gets in. If the reviewed head disappeared from the branch (force-push), `fetch` fetches the commit by sha and pins it at `refs/bugbot/.../reviewed`.

| Situation | Result |
|---|---|
| no commit since the bot's last review | `unchanged`: nothing to review again |
| author only merged or rebased the base, without conflict | empty delta → `unchanged` |
| author resolved a conflict touching lines of the PR | only the files and lines of the resolution |
| author pushed a fix commit | only the fix |

**Round scope** (`triage.json` → `round.scope`, on the card as `round  N  <scope>`):

| Scope | When | What runs |
|---|---|---|
| `full` | rounds before `review.delta_from_round` (default 3), `--full`, or no delta available | finders on the whole diff |
| `delta` | from `review.delta_from_round` on | finders only on the delta's files; kept findings of the previous round in files outside the delta are **carried** without a new verification (`carried_from`) |
| `unchanged` | no commit since the last round, or commits that did not change the PR's diff | no finder; every kept finding is carried |
| `capped` | after `review.max_rounds` (default 3) | nothing; the card suggests rethinking the approach with the author; `--full` reviews anyway |

Only what was published counts as a round: a PR without a bot review carrying the marker is round 1, with a full review. `triage.json` `rounds[]` remains the history of **your local decisions** (for the `/retro`), but does not define the round.

**When publishing a new round** (`publish`; details in **`bugbot publish`**):
- the bot's previous review is only **minimized as `OUTDATED`** when neither it nor the new one reviewed anything (`unchanged` or `SKIPPED`): this avoids piling up "nothing to review" on every push. A review that analyzed code is **never** minimized, because whoever reads later needs to see what was said at that commit;
- **inline threads are never minimized**: they are findings, and "every finding has to be answered". GitHub already marks them outdated when the line changes;
- a finding already posted (same fingerprint) is not posted again.

A new local round starts clean: `fetch` deletes `candidates/`, `verdicts/` and `preflight.json` when the head changed, or when the previous head cannot be read from `meta.json` (and recreates the empty folders), and keeps the previous `findings.json` as `previous-findings.json`, which carrying reads from.

## Review agent

`.agents/bugbot.md` is the agent that does the part the CLI does not: reading code. It drives the CLI, reviews and writes `findings.json`; it publishes only through `bugbot publish`, after showing the `--dry-run` and you saying yes.

Agents in `.agents/`:

| Agent | Model | Role |
|---|---|---|
| `bugbot.md` | sonnet | orchestrator: CLI → triage → finders → (verify) → report; does not review code |
| `triage.md` | sonnet | second opinion on the triage weight, one level up or down; also writes the pre-flight plan (see **Pre-flight**) |
| `retro.md` | sonnet | runs the `bugbot-retro` skill and persists the report; automatic after a published APPROVE when configured |
| `finder-bugs.md` | opus | behavior bugs and, after them, documentation that does not match the code it describes (a symbol that is gone, wrongly described behavior, an inconsistent sample), including code from another workspace repo; one run per production domain (+ `cross-domain` in deep) |
| `finder-security.md` | opus | security on the whole diff; one run per PR |
| `finder-convention.md` | sonnet | only quotable rules from `REVIEW.md`/`AGENTS.md`/`CLAUDE.md`; one run per PR |
| `verifier.md` | opus | judges **one** candidate, alone: is it real? is it from this PR? does it matter? (`keep`/`cut`/`unsure`); 1 verdict in quick, 2 in standard/deep, a 3rd on a tie in deep |
| `shared/review-rules.md` | — | rules for **every** sub-agent that reads a PR (`triage`, finders, `verifier`): where to read code and instructions, security, severity, validation |
| `shared/finder-rules.md` | — | rules shared by the three finders (input, base rules, what not to report, candidate format, output): every finder reads it first |

The finders are separate files so each one has **its own model** (bugs and security on opus for sensitivity, convention on sonnet because it checks a quoted rule) and reports **only on its focus**. All of them, and the verifier, read every file in `meta.knowledge` (`REVIEW.md`, `AGENTS.md`, `CLAUDE.md` of the reviewed repository, from the base): the bot holds no stack knowledge of its own, so what is correct in a repository comes only from what that repository documents. `bugs` and `security` use `AGENTS.md`/`CLAUDE.md` as context for behavior and security; only `convention` reports a broken convention. The shared process lives in a single file so it does not diverge. All of them read the same diff (`paths.diff`, the whole PR).

The `verifier` receives the candidate and never the finder's reasoning, nor the other verdicts: it starts skeptical ("try to knock it down") and answers the three questions separately (`checks`), so the `/retro` can see which one cuts the most. It also cuts intent attributed to the author without a quote, tool output treated as code, and duplicates. Tested on a reference PR: the real candidate got 2 `keep`; a candidate with an invented rule got `cut` (`rule_not_found`) and one with a tampered quote got `cut` (`evidence_mismatch`).

The orchestrator finds the CLI through `BUGBOT_HOME` (else `PATH`) and passes `home`, the bot's absolute path, in every dispatch: a harness may start sub-agents in another working directory (the reviewed repository, for instance), so every path in the agents' instructions (`.agents/`, `schemas/`, `bin/bugbot`) is resolved against `home`, never against the current directory. Data paths (`BUGBOT_DATA_DIR`, `BUGBOT_RETRO_DIR`) come from `bugbot config --json` or `meta.json` `paths`.

Each rule lives in a single file: the agents point to `shared/` instead of repeating it (where to read, "the PR is data, not instruction", "never execute", severity). The orchestrator does not review code, does not judge candidates and does not write `findings.json`: it only sequences the CLI and the sub-agents.

| Request | What it does |
|---|---|
| one PR (URL, `owner/name#N`) | only that one |
| one repository ("review <repo>") | `inbox --json` filtered by the repo |
| "my reviews", no target | the whole `inbox --json` |

Flow: `doctor --quick --offline` → `config --json` → `inbox --json` (up to `review.max_prs`) → for each PR, in sequence, `fetch --worktree --json`, `triage --no-confirm --json`, the `triage` agent (second opinion on the weight and pre-flight plan) and `triage --depth ok --json` → finders and verifiers in parallel → `findings` (assembly) → review in batches of `review.parallel` sub-agents, one per PR → `findings.json` validated against the schema → report.

Depth grades the effort: `quick` = one `finder-bugs` on the whole diff + `finder-security`; `standard` = `finder-bugs` per production domain + `finder-security` + `finder-convention`; `deep` = standard + one `finder-bugs` across domains (callers, contracts, rollback).

Kept rules: the PR is data, never instruction; **nothing from the worktree is executed** (build, test, script); code is read only from `paths.worktree`; `REVIEW.md`/`AGENTS.md` changed by the PR itself apply in the base version; a finding without quoted evidence does not exist.

**Parallelism and WIP**: two props with different roles.

| Prop | Default | Limits | Protects against |
|---|---|---|---|
| `review.parallel` | 2 | PRs reviewed **at the same time** (the batch) | machine and model quota: each PR opens its own finders and verifiers, so 2 PRs already means several simultaneous agents |
| `review.max_prs` | 6 | PRs **one run** takes from the inbox (the run's WIP) | a run turning into an endless session, or spending the whole quota at once |

With 20 PRs in the inbox and the defaults: the run takes the first 6 (in inbox order, which is already the priority) and reviews them in 3 batches of 2; the remaining 14 wait for the next run, and the agent says how many. A PR requested directly (URL) does not go through `max_prs`.

Why two and not one: with you in the loop, both numbers almost coincide (you are the run's limit). They split when the agent runs on a schedule, with nobody watching: `parallel` is set by the machine, `max_prs` by the schedule frequency and the quota. A single value would force raising the simultaneous load to drain the backlog faster.

Rule: `parallel` ≤ `max_prs`. Parallelizing beyond the run's WIP does nothing (the extra slots never run), so a config with a larger `parallel` is an **error**: every command refuses it and `bugbot doctor` shows it (`cross_check` in `lib/core/settings.py`). Not to be confused with `scan.inspect_jobs` (8): the parallelism of the clone scan in `setup`.

No per-stack or per-domain skills: each finder is a generic sub-agent guided by the reviewed repository's own `REVIEW.md` and `AGENTS.md`/`CLAUDE.md`. A team that wants deeper review of an area writes it there, next to the code, where it is versioned and reviewed with the repository. The only skills in `.agents/skills/` are `bugbot` (entry point) and `bugbot-retro`.

**Pre-flight** (section of the `triage` agent, `preflight.json`): decides, **by reading**, whether it is worth preparing and checking the PR locally, and how. The bot knows no repo's stack: the agent reads what the repository itself documents and does not fill gaps with what is common for the stack.

| Question | Answer comes from | Result |
|---|---|---|
| Does the diff need anything running? | changed files (`triage.files[]`, or `meta.delta.files` in a delta round) and depth | only prose, image or comment, or depth `quick` → `none` |
| Is running allowed? | `triage.local_exec` and the files the PR changes | `sensitive` with `local_exec: deny`, or the PR changes a step's command, script or document → `blocked` |
| How does the repo prepare and check itself? | `meta.knowledge` (`REVIEW.md` wins), `CONTRIBUTING.md`, `README.md` and development docs, nearest to the files first; the build entry points they mention. Everything **from the base** | steps with a literal quote → `proposed`; nothing documented → `unknown`, and the reason becomes a suggestion for the repo |

Timing: in the `triage` agent, right after `--assess` and before the finders. At that point the assessed depth, the changed files, `local_exec` and the knowledge already exist, and the agent already reads them; any later, a check's result would no longer serve the finders. It is an instruction, not a sub-agent of its own: it needs neither another dispatch nor another context. It does not run in `unchanged` or `capped` rounds, because `triage` is not dispatched in them.

Rules: the agent never executes anything, not even to "check"; commands come only from the base, never from the worktree or the PR text; setup comes before checks and the check is the narrowest one covering the changed files (the module's tests, not the repo's); `reuses` only points to caches that hold downloads or build outputs keyed by their input (the build tool's cache, the package manager's download cache), never the dependencies installed in the clone (the discarded attempt in **Open items** shows why). Today the plan is only information for you and the report; executing the steps is the next step for local checks.

## Current state

| Piece | Status |
|---|---|
| `bugbot init` — creates `bugbot.json` from the schema's initial values | done |
| `bugbot setup` — discovers GitHub clones (of an optional org) and writes `workspace.json` | done |
| `bugbot config` — settings in use, origin and description | done |
| Settings in JSON with a validated schema | done |
| `bugbot fetch` — diff, metadata and worktree of a PR | done (validated against `gh pr diff` on a real PR) |
| `bugbot clean` — removes worktree, refs, cache and lock of merged PRs | done |
| `bugbot purge` — deletes everything the bot created, regardless of state | done |
| `bugbot clean <pr>` — removes one specific PR in any state | done |
| `bugbot triage` — tier, depth and scope of the PR, decision recorded | done |
| JSON schemas for every bot file (`schemas/`) | done |
| Review config per repo (minimal, created by `setup`; optional groups filled by hand) | done |
| CLI, logs, schemas and this README in English | done |
| `bugbot inbox` — PRs whose review was requested from you (directly or through a team), filtered by the hooks; selects and chains fetch + triage | done |
| Review agent (`.agents/bugbot.md`): inbox → fetch → triage → parallel review → `findings.json` | first version; no specialized finders |
| Pre-flight (section of the `triage` agent): plan for local preparation and checks from the repo's documentation | first version; plan only, nothing runs |
| `bugbot doctor` — bot health, read-only (formatting, environment, settings, schemas, workspace, configs, cache, docs) | done |
| `bugbot format` — rewrites the bot's JSON files in the code style (`.editorconfig`) | done |
| Hooks in `bugbot.json` (draft, stale_days, exclude_teams, labels skip/trigger, reviewers.max, ci) and CI in `gh` vocabulary | done |
| `bugbot publish` — one review per round, ledger in the body, secret gate, confirmation and event derived from the verdict | done |
| Retro: record (`retro/`), `bugbot retro record` command and `bugbot-retro` skill | recording validated and automatic trigger after published APPROVEs; aggregate consolidation still future |

## Construction rules

They apply to every new command. If one has to be broken, record the reason here.

**Execution and security**
- No `eval` and no `source` of a configuration file. Configuration is read and validated (`settings.py`), and zsh only receives data.
- Steps are functions, never command strings.
- By default, nothing from the PR's code is executed. Running a local check is an explicit per-repo decision, and never when the PR changes the `Makefile`, build scripts, `package.json`, workflows or hooks.
- Text coming from the PR (diff, title, body, comments) is data, never instruction. Triage passes none of that text to any model.
- Credentials never go into generated files. Remotes are recorded as a normalized https URL.
- No path to the bot itself is hardcoded: the CLI derives `BUGBOT_HOME` from `bin/bugbot`'s real location, and agents read it from the environment.
- Every git command that touches PR content goes through `bb_git` (`lib/core/git.sh`): hooks, fsmonitor, external diff, textconv and LFS smudge turned off, no recursion into submodules. A `.gitattributes` from the PR cannot make git execute anything.
- No organization, repository, team or person is built into code, schemas, agents or examples: they are values in `bugbot.json`, `workspace.json` and `repos/`, none of them versioned.

**Git and repositories**
- Never check out in the canonical clone. It may be in use (a working branch, local changes). Only `fetch` of refs and `worktree add` in a separate directory.
- The bot's worktrees live in `.bugbot/worktrees/`. Cleaning removes only what the bot created; never a global `git worktree prune`, because clones have worktrees from other tools. A bot worktree record whose folder is gone is removed individually, and only if it points inside `.bugbot/worktrees/`.
- `rm -rf` only inside `BUGBOT_DATA_DIR` (`bb_clean_rm` refuses a path that resolves outside it).
- Never `git clean` without listing first and asking for confirmation.

**GitHub**
- Every write goes out as the `gh` user of whoever runs it. Before writing, confirm `gh api user` and that no `GH_TOKEN`/`GITHUB_TOKEN` in the environment overrides the login. Nothing hardcoded to a user.
- The bot only sends `APPROVE`, `COMMENT` or `REQUEST_CHANGES` after the person's explicit confirmation; it does not apply labels to other teams' PRs.

**Output and UX**
- UI on stderr, data on stdout (`--json`, TSV). Without a terminal: no colors or animation, and prompts refuse, naming the equivalent flag.
- Everything that changes a file or GitHub has `--plan`/dry-run and confirmation; the confirmation default is "no".
- Exit codes: `0` success, `1` failure or cancellation, `2` usage or invalid configuration, `130` Ctrl-C.
- File writes are atomic (temp + rename).

**Language**
- CLI, messages, logs, step names, schemas, generated files and this README: **English**. Mixing languages in the output becomes a mess. Only what is posted on a PR follows `review.language`.

**Data format**
- Every JSON the bot reads or writes has a schema in `schemas/<name>.schema.json` (JSON Schema Draft 2020-12). The schema is the source of truth for the format: code validates against it before using or writing, and the file points to it in `$schema` (editors validate and complete).
- Own validator in `lib/core/schema.py`, stdlib, with only the subset of keywords the schemas use. An unsupported keyword in a schema is an error at load time, never silently ignored.
- The bot is project-agnostic: no code knows a specific repository. What changes between repos are **values** in `repos/<owner>/<name>.json`, always with the same keys.

**Dependencies**
- Only `zsh`, `git`, `gh` and `python3` ≥ 3.9 (stdlib). The script is shared and has to run on macOS's system Python.

## Vocabulary

One term per concept, aligned with Anthropic's article.

| Term | Meaning |
|---|---|
| Critical / High / Medium / Low | Severity of the finding; the level sets the PR's aggregate decision |
| Pre-existing | Tag of a finding the PR did not introduce |
| Tier: low / medium / high | Risk of the **change**, computed deterministically |
| Depth: Quick / Standard / Deep | Review effort. Derived from the tier, with the reviewer's override |
| Finder | Agent that looks for problems in a domain, in a clean context |
| Verifier | Agent that votes on whether a finding is real. Tries to refute it and receives only the finding, the excerpt and the rule |
| Check | Deterministic execution: CI, a make target, snapshot, smoke |
| `REVIEW.md` | Review instructions per repo (format to be defined) |
| Rounds-to-pass | Reviews, on different shas, until no Critical, High or Medium is open |
| Readiness | New Critical/High requests changes; Medium asks for a comment; only Low or no finding approves |

## Design decisions

**Triage** (`bugbot triage <pr>`, `lib/triage/triage.py`; deterministic, before any LLM)
- Inputs: `meta.json` from fetch, the repo config (`repos/<owner>/<name>.json`), CI state (`statusCheckRollup`) and the previous triage. It **never** reads the PR's title, body or comments: nothing in it can be steered by PR text.
- Tier (inspired by the Claude Code team's "risk tiers, set from the change" and "match review effort to risk"; risk comes from what the change touches, not from the author):
  - **base**: highest `risk` among the domains of the reviewable files (a domain without `risk` = medium; without domains, everything medium). Files in `tests` do not count towards the base: a tests-only PR = low.
  - **`sensitive`**: a matching file → high **with a deep floor**. It is the only path to a floor. A test file never triggers `sensitive`. A renamed file also counts by its old path.
  - **size**: **production** lines (outside low domains, `tests` and `ignore`) above `large_diff` (default 1000) raise one level.
  - **breadth**: production domains touched ≥ `broad_change` raise one level (0 = off; only turn it on with calibrated domains).
  - Suggested depth: low → quick, medium → standard, high → deep. A high tier by domain, size or breadth **suggests** deep, without a floor: picking standard raises no warning.
- **Second opinion (the `triage` agent, `.agents/triage.md`)**, the Claude Code team's "second classifier": the rules see paths and sizes; the agent reads the change and says whether the weight fits **this PR**. It keeps, raises or lowers one level, always with a reason, recorded by `triage --assess <tier> <depth> --reason "..."` in `rounds[-1].agent`. The card shows it (`agent  lower  high/deep → medium/standard  <reason>`) and starts suggesting the agent's depth; the decision is still yours.
  - Limits applied by the CLI (outside them the assessment is refused, exit 2, never adjusted): lower at most **one level** of tier and of depth; **never below the floor** of a `sensitive` (nor below the high tier it gives): only you go below that, with `--depth`, and it is recorded as `below_floor`; one direction only (it does not raise one and lower the other); reason required, one line.
  - It applies to the assessed head: a new commit opens another round and the previous assessment is not reused.
  - Calibration: the agent adjusts **one PR**; the rule only changes when you change `repos/*.json`. `triage.json` keeps the three voices side by side — rules (top level), agent (`rounds[].agent`), you (`rounds[].decision`) — and that is what the `/retro` will cross per rule: a domain the agent almost always lowers, with a clean review and you agreeing, is too strict ("start strict and loosen with evidence").
- Card: tier with reason, depth, diff, scope per domain, local execution allowed or blocked (and by which files), CI (green, failing, running), round and warnings (draft, closed PR, CI failing or running, repo without config, binary). One-key answer: enter accepts, `q`/`s`/`d` choose, `a` or Esc aborts; other keys are ignored. Without a terminal: `--depth ok|quick|standard|deep|abort` or `--no-confirm`.
- Repo without config: everything becomes medium with scope by top-level folder, and the card warns.
- Output: `triage.json` in the PR's cache. `rounds[]` keeps, per sha, the decision (`depth`, `suggested`, `override`, `below_floor`): it is the `retro`'s input to calibrate triage. A new sha opens a new round with a delta re-review note; answering again on the same sha replaces the decision.
- The author's login is recorded but does not enter the computation. The author's familiarity with the stack is left for later.
- Evolution: starts manual; `auto_confirm` per repo when the override rate is low. High is never automatic.
- CI: `SUCCESS` counts as green; `NEUTRAL` and `SKIPPED` do not block; failure, timeout, cancelled and the like count as failing.

**Review config per repo** (`repos/<owner>/<name>.json`, `schemas/repo.schema.json`, `lib/core/repoconfig.py`)

Data for the scripts, separate from `REVIEW.md` (prose for the finders). Same keys for every repo; only the values change.

| Key | Use |
|---|---|
| `repo` | `owner/name` the config belongs to |
| `$metadata` | About the file: `schema_version` and `created_at` |
| `domains` | **Ordered** list `[{name, risk, paths}]`; the first match wins (a JSON object does not guarantee order, hence a list). Sets the file's risk, the scope and which finders to dispatch |
| `sensitive` | `[{glob, reason, local_exec}]`. High with a deep floor; `local_exec: deny` blocks local execution when the PR touches the file (see **Local execution** below) |
| `tests` | Test files: reviewed, but outside risk, size and `sensitive`; production code changed without a test changed raises a warning |
| `ignore` | Neither counted nor reviewed (e.g. generated output). Binaries (detected by `git`, no glob) are already left out on their own, except those matching `sensitive`: they keep the deep floor and raise a warning to check by hand |
| `large_diff` | Overrides the global limit |

Globs: `**` crosses directories, `*` and `?` stay within a segment, a trailing slash matches everything below.

**Local execution (`local_exec`)**: today the bot only **reads** the PR's code (diff and worktree); it runs nothing from it. When local checks exist (build, tests, lint run by the bot in the PR's worktree, see "Open items"), running means executing code the PR's author wrote, on your machine, with your credentials. For most code that is acceptable: the source comes from the PR, but the build commands come from the base branch. The problem is the files that **define what the build executes**: if the PR changes the `Makefile`, `package.json` (`preinstall`/`postinstall` scripts), a babel plugin or a git hook, running the build is running the command the author chose. `local_exec: deny` on a `sensitive` rule marks those files: a PR that touches any of them never runs a local check, it is only read. The card shows `local  checks blocked` and by which file; `triage.json` records it in `local_exec.denied_by`. Rule of thumb: `deny` on everything the build, the install or a hook executes; `allow` (default) on the rest. Today blocking only exists inside `sensitive`, so removing a glob from `sensitive` (for example `scripts/**`) also allows local execution of those files.

**Creation by `setup`** (`lib/setup/repo_config.py template`, final step of `setup`): for each repo in `workspace.json`, it creates the config **only when the file does not exist**, with only `$schema`, `$metadata` and `repo`. The review groups are all optional and are added by hand, one by one, when decided (format in the schema's `<details>` below). Nothing is inferred from the repo: a wrong preset (a "sensitive" file that is not, a "low risk" path that is not) changes the tier of every PR without anyone noticing, and looks identical to a decision. An existing config is never touched; `bugbot doctor` is what makes sure it stays adequate.

A missing (or empty) group = not configured, and that shows: the triage card warns (`domains not configured, every file medium risk, scope by top-level directory`; `sensitive not configured, no file sets a floor`) and `doctor` lists each repo's empty groups.

`repo_config.py check --clone <path> --config <file>` (the same one `doctor` uses) points out globs that match no file (a typo, a renamed folder) and, if there are `domains`, files outside any domain.

**Glob catalog** (advisory: nothing applies this automatically; copy into `sensitive` what makes sense for the repo)

| Glob | Reason | `local_exec` |
|---|---|---|
| `.github/workflows/**`, `.github/actions/**`, `.gitlab-ci.yml`, `Jenkinsfile` | CI | deny |
| `**/CODEOWNERS` | review ownership | allow |
| `Makefile`, `**/Makefile`, `**/*.mk` | build | deny |
| `.githooks/**`, `.husky/**` | git hooks | deny |
| `**/package.json`, `**/.npmrc` | npm manifest/scripts, registry | deny |
| `**/package-lock.json`, `**/pnpm-lock.yaml`, `**/yarn.lock` | JS lockfiles | allow |
| `**/build.gradle(.kts)`, `**/settings.gradle(.kts)`, `**/gradle.properties`, `gradle/wrapper/**`, `build-logic/**`, `buildSrc/**` | Gradle build | deny |
| `**/libs.versions.toml` | Gradle version catalog | allow |
| `**/Podfile`, `**/Gemfile`, `**/Package.swift` | CocoaPods/Bundler/SwiftPM manifests | deny |
| `**/Podfile.lock`, `**/Gemfile.lock`, `**/Package.resolved` | lockfiles | allow |
| `MODULE.bazel`, `WORKSPACE`, `**/*.bzl`, `.bazelrc`, `.bazelversion` | Bazel | deny |
| `**/go.mod` / `**/go.sum` | Go module / checksums | deny / allow |
| `**/Dockerfile`, `**/*.xcconfig` | image, Xcode build settings | deny |
| `**/*.entitlements`, `**/AndroidManifest.xml`, `**/proguard-rules.pro` | permissions, components, shrinking | allow |
| folder of `.patch` files with diffs to `node_modules/` (e.g. `patches/**`) | dependency patches | deny |

Also usually worth it, but repo-dependent: public API snapshots and check baselines (`sensitive`); `**/*.md`, `docs/**`, images (a `docs` domain with `risk: low`); `tests` globs per framework (table below); generated and vendored code (`ignore`). Files that change the behavior of AI agents (skills, agents, `AGENTS.md`, `REVIEW.md`, permissions and hooks such as `.claude/settings.json`) also deserve `sensitive`. Detecting them by name misfired on real repos (it caught Android's `baseline-prof.txt` and e2e fixtures), so it is never automatic.

Reference `tests` globs. With `tests` empty or missing a framework, its tests count as production: they inflate the tier and raise the false warning "no test file changed".

| Framework | Globs | Pitfall |
|---|---|---|
| JS/TS (Jest, Vitest, Playwright) | `**/__tests__/**`, `**/*.test.*`, `**/*.spec.*`, `**/tests/**`, `**/e2e/**` | `*.spec.md` specs in docs; check with `doctor` |
| Kotlin/Android (Gradle) | `**/src/test*/**`, `**/src/androidTest*/**` | `test*` covers `testDebug`/`testFixtures`; `**/test/**` catches Gradle modules with that name and `*Test.kt` catches `.idea` templates |
| Swift/iOS (XCTest) | `**/*Tests/**` | covers `UITests`; `*Tests.swift` catches `AppDelegateForTests.swift`, which is production |
| Go | `**/*_test.go` | `*_test.*` catches `.md`, `.xml` and `.json` |
| Test-only libs | their path (e.g. `common/test-libs/**`) | — |

`doctor` compares the globs with the clone's default branch, not with the local branch: a glob that only matches on a working branch shows up as `match no file`.

**Review**
- Findings with executable evidence do not go through a vote; judgment ones do (start with 3 verifiers and majority, calibrate against the reviewer's decision).
- A cap of 3 rounds. From the 2nd on, re-review only the new diff; do not repeat a finding already posted; point out the class of the bug, not just the line.
- Pre-existing: by `git blame` in Quick/Standard; by a check on the base, only for potentially blocking findings, in Deep.

**Publishing**
- One review per round, with every inline comment in a single `POST`. Anchors validated against the hunk before sending; a finding outside the diff goes to a collapsed section in the body.
- Fingerprint per finding, recorded in an HTML comment inside each inline comment. Local state is the primary source.
- A versioned marker (`review.marker_version`) separates assisted review from manual review, since both go out as the same user, and says which head each round was made on (see **Review rounds**).
- Secret gate before publishing. No bot header, no severity badge image, no labels.
- The publish dry-run is the confirmation screen: the reviewer chooses by ID which findings go in.

**Learning**

```
publish APPROVE  →  retro agent / bugbot-retro skill  →  `bugbot retro record` (append-only)  →  future consolidation
```

- `retro/` is the permanent record of retrospectives per PR, in `retro/<owner>/<name>/pr-<N>.jsonl`: one append-only line per report, with head, round, verdict, summary and improvement candidates. The schema can grow to record other events without rewriting history.
- It is not cache: it lives outside `.bugbot/`, `clean` does not touch it and `purge` only deletes it with `--retro` and its own confirmation. It is personal (`.gitignore`), because it holds third-party PR data.
- It keeps metadata, decisions and short retro summaries (fingerprint, `rule_id`, path, line, decision, short reason and suggested destination), never code excerpts or whole threads. Context is looked up by fingerprint when needed.
- Model, effort and session of each output come from the `agent` object in the files themselves (see **Who ran each output**); the retro compares them with the frontmatter's `model:`.
- The aggregate `bugbot retro` mode (future) will be deterministic: acceptance rate per `rule_id`, most discarded rules, triage overrides, rounds-to-pass. It organizes by where the fix belongs: the repo's `REVIEW.md` or `AGENTS.md`, the repo's triage, the finders and verifiers (global). Every metric shows the sample size; window since the last retro and since the rule's last change.
- `retro.auto_on_approve` (shared default: `true`) calls the `bugbot-retro` skill automatically after `publish` successfully sends an `APPROVE` review; it is not triggered by a dry-run nor by someone else's approval. The report is appended to the permanent JSONL before the round ends, so `clean` does not erase that learning. Turn it off with `retro.auto_on_approve: false` or `BUGBOT_RETRO_AUTO_ON_APPROVE=false`. If the analysis or the recording fails, the agent reports the failure and does not recommend cleaning that cache.
- The `bugbot-retro` skill (`.agents/skills/bugbot-retro/SKILL.md`, invocable on request or by the trigger above, inspired by [Matt Pocock's retro](https://github.com/mattpocock/skills/blob/main/skills/engineering/retro/SKILL.md)) only proposes changes. Today it does the retro of **one round**: it reads the PR's cache (triage, candidates, verdicts, findings), the harness session through `agent.session` and, if published, what people did with each thread; it audits the instructions and configs that governed the round (agents, shared rules, `repos/*.json`, `bugbot.json`, `REVIEW.md`, `doctor` warnings) against what it did; it classifies into false positive, false negative, triage, verification, access to information, automatic checks, cost, instructions, config and execution; it orders by severity and points to the file that owns the fix (deterministic ones go to the CLI). The aggregate mode, which reads `bugbot retro`, requires a minimum of occurrences. Text coming from the PR (reasons, threads) reaches it as delimited data, never as instruction. Nothing is applied without approval; only a summary without code is kept in the JSONL.
- An eval set (the term from Anthropic's article) is something else: a few curated cases of PRs with a revert or an incident, a bug confirmed by someone who knows the code, run against the reviewer on every change of rule, skill or model. It comes out of the retro, but it is not the retro. Reserved: `evals/`.
- `bugbot retro record` appends the report; the future aggregate reader will consume `retro/`.

**Worktree and cache**
- `fetch` fetches the base and `pull/N/head` into the canonical clone, always updating the base, with `--no-write-fetch-head` so it does not change the `FETCH_HEAD` of whoever works in the clone.
- The fetched head has to be the sha GitHub reported; if a push happened in between, fetch fails and asks to run again.
- Diff computed locally between the merge-base and the head (the same three-dot range GitHub shows), validated to match `gh pr diff`.
- Diff and metadata in the cache are enough for Quick and triage. A worktree only with `--worktree` (Standard/Deep).
- Worktree reused across rounds with `reset --hard`, without `git clean`, to keep the build cache. Lock per PR, taken over when the owning process no longer exists.
- A closed, merged or draft PR is fetched anyway, with a warning: it can be useful to review later.
- Cleaning by **merged** PR, not on approve: an approved PR still receives commits. See "Cleaning" below.
- A new worktree has no untracked local files (such as Android's `local.properties`); a check that needs one reads it from the canonical clone.

**Names per PR** (`lib/core/pr.sh`, single source; every command uses `bb_pr_names`)

PR numbers repeat across repos, so the repo is part of every name. Joining owner and name with a hyphen also collides (`a-b/c` and `a/b-c` would both become `a-b-c`), so names are nested, since `/` appears in neither owner nor name.

| What | Format |
|---|---|
| Cache, worktree, lock | `.bugbot/{cache,worktrees,state/locks}/<owner>/<name>/pr-<N>/` |
| Refs in the clone | `refs/bugbot/<owner>/<name>/pull/<N>/{head,base}` |

- Owner and name lowercased (GitHub does not distinguish) and the number normalized (`0262` = `262`).
- Parts git does not accept in a ref (a name starting with `.`, ending in `.` or `.lock`, or containing `..`) are escaped with `+`, which never appears in a GitHub name. The path on disk uses the original name; `.` and `..` are refused.
- Refs live outside `refs/heads/`: they do not show up in `git branch` and do not get in the way of work in the clone.
- `doctor` (group `cache`) warns about anything in `cache/`, `worktrees/` or `state/locks/` outside this layout (a file from an older layout, a loose file, an empty folder): `clean` cannot see it, `purge` removes it.

**Detached worktree and the branch name**

The worktree is on a detached HEAD at the PR's sha. No branch is created, neither the author's nor one of the bot's: the author's may be checked out elsewhere, collides across forks and disappears after the merge, and a bot branch would pollute the clone's `git branch`. In detached mode, `git rev-parse --abbrev-ref HEAD` returns `HEAD` and `git branch --show-current` returns empty.

What tends to break in detached mode, and what to look at in a new repo before enabling local checks:

| Pattern in the script | Effect in detached mode |
|---|---|
| `GIT_BRANCH` from `git branch -r --contains HEAD` | only works if the commit is on a fetched remote branch, which is not true for `pull/N/head` |
| `git rev-parse --abbrev-ref HEAD` / `git branch --show-current` | returns `HEAD` / empty |
| diff by branch name (`git diff <branch> <merge-base>`) | with `HEAD` instead of the name it gives the right result |
| default base by upstream (`origin/dev`) | right only when the PR's base is that branch |
| branch name validation (release-like) | blocks; needs `GIT_BRANCH` in the environment |
| commit hooks | irrelevant: the bot does not commit and runs git without hooks |

Other uses found are about release, push or branch creation, out of review scope. Rule: when a local check needs the branch name, it gets `GIT_BRANCH=<head.ref from meta.json>` (and whatever else the script reads) only in that check's environment, declared in the repo's `REVIEW.md`. The worktree stays detached.

**Cleaning**

| | `clean` | `purge` |
|---|---|---|
| Criterion | PR **merged** on GitHub | None: everything |
| Removes per PR | worktree (and its record in the clone), refs, cache, lock | same, for all |
| Also removes | bot worktree records whose folder is gone | every ref under `refs/bugbot/`, logs of previous runs, `state/`; `retro/` only with `--retro` |
| Keeps | open PR, closed without merge (can be reopened), unreadable state, in use; always `retro/` | `retro/` (without `--retro`), `workspace.json`, `bugbot.json`, `bugbot.local.json` |
| PR in use by another process | Skips the PR | Aborts before deleting anything |
| Confirmation | `[y/N]`, or `--yes` | Type `purge`, or `--yes`; with `--retro`, also type `retro`, or `--yes-retro` |
| `--plan` | Lists and removes nothing | Lists and deletes nothing |

The inventory (`lib/clean/inventory.py`, read-only) joins three sources so nothing is left behind: the data folder, the clones in `workspace.json` plus those recorded in each cached `meta.json` (a repo that left the workspace still has refs in the clone), and, inside each clone, `refs/bugbot/` and the worktree records pointing into the bot's folder. The PR's state comes from `GET /repos/{repo}/pulls/{n}` (field `merged`). Every path and ref removed comes from `bb_pr_names`.

### `fetch` output

| File | Content |
|---|---|
| `diff.patch` | Unified diff, without textconv or external diff |
| `files.txt` | One path per line |
| `meta.json` | Repo, PR, title, author, state, draft, fork, head (sha, branch, local ref), base (branch, reported sha and fetched sha), merge-base, statistics (local and GitHub's), files with status `A/M/D/R/C`, lines and whether binary, and the clone, diff and worktree paths |

The PR's title and body stay only in `meta.json`; the shell never receives that text. When local statistics differ from GitHub's, the step ends with a warning (▲).

## Configuration

Three files you edit or generate, each with a role:

| File | Question it answers | How to change it |
|---|---|---|
| `bugbot.json` / `bugbot.local.json` | How does the bot behave in any repo? (optional org, scan, triage limits, hooks) | `bugbot init` creates `bugbot.json`; then edit by hand; `bugbot config` shows the effect; a `BUGBOT_*` variable beats both |
| `workspace.json` | Which repos does this machine review and where are the clones? | `bugbot setup` (manual edits are preserved) |
| `repos/<owner>/<name>.json` | What is the risk of each part of this repo? | Edit by hand after `setup` |

Format, precedence, examples and how to evolve each one are in the **Schemas** section below. When a schema changes, update the matching `<details>`; `bugbot doctor` (group `docs`) fails if any key is left undocumented.

## Schemas

Every JSON file the bot reads or writes has a schema in `schemas/<name>.schema.json` (JSON Schema Draft 2020-12). The schema is the **source of truth for the format**: code validates against it before using or writing, and every file points to it in `$schema`, which makes the editor validate and complete while you edit.

Overview, in the order the files appear in the flow:

```
init ───► bugbot.json ────┐
bugbot.local.json ────────┤ settings   how the bot behaves (global)
                          │
setup ──► workspace.json  │ workspace  which repos this machine reviews and where the clones are
      └─► repos/*.json    │ repo       review rules of each repo (domains, risk, sensitive)
                          │
inbox ──► inbox.json      │ inbox      PRs waiting for your review, already filtered
fetch ──► meta.json       │ meta       facts of a PR (sha, files, lines)
triage ─► triage.json     │ triage     tier, depth, scope and your decision per sha
triage ─► preflight.json  │ preflight  how to prepare and check the PR locally (plan only)
finder ─► candidates/*.json │ candidates what each finder found, before verification
verifier ► verdicts/*.json │ verdicts   one verdict per candidate per verifier
findings ► findings.json │ findings   what the review found in one round (kept and cut)
agent ──► translate.json │ translate  the review text in the language of review.language
publish ► review on the PR │          body + ledger + marker, inline comments
```

| Schema | File | Written by | Read by | Scope | Shared? |
|---|---|---|---|---|---|
| `settings` | `bugbot.json`, `bugbot.local.json` | `init`, you | every command | global | no (written by `init`, adjusted by you) |
| `workspace` | `workspace.json` | `setup` | `fetch`, `setup`, `clean` | machine | no (local paths and login) |
| `repo` | `repos/<owner>/<name>.json` | `setup` (minimal), you (calibrate) | `triage`, `inbox`, `doctor` | repository | no (personal calibration; copy from someone who already calibrated) |
| `meta` | `.bugbot/cache/<owner>/<name>/pr-<N>/meta.json` | `fetch` | `triage`, `clean` | PR | no (cache) |
| `triage` | `.bugbot/cache/<owner>/<name>/pr-<N>/triage.json` | `triage` | `inbox`, agent, `retro` (future) | PR | no (cache) |
| `inbox` | `.bugbot/runs/<date>-inbox/inbox.json` | `inbox` | you, agent (`--json`) | run | no (rebuilt on every call) |
| `preflight` | `.bugbot/cache/<owner>/<name>/pr-<N>/preflight.json` | `triage` agent | you, report; local checks (future) | round (head sha) | no (cache) |
| `verdicts` | `.bugbot/cache/<owner>/<name>/pr-<N>/verdicts/<finder>.<index>.<verifier>.json` | `verifier` agent | `findings.json` assembly | round (head sha) | no (cache) |
| `candidates` | `.bugbot/cache/<owner>/<name>/pr-<N>/candidates/<finder>.json` | `finder-*` agents | verifiers, `findings.json` assembly | round (head sha) | no (cache) |
| `findings` | `.bugbot/cache/<owner>/<name>/pr-<N>/findings.json` | `bugbot findings` | `publish` | round (head sha) | no (cache) |
| `translate` | `.bugbot/cache/<owner>/<name>/pr-<N>/translate.json` | `bugbot` agent | `publish` | round (head sha) | no (cache) |

Conventions shared by all:
- `"version"` in every generated file: current schemas use 1, except `candidates`, `verdicts` and `findings`, which use 2 because of the severity migration; v1 caches are read and converted in memory (`Important`→`High`, `Nit`→`Low`). An old `bugbot.json` with `max_nits` is also read as `max_lows`.
- `additionalProperties: false` on every object: an unknown key is an error, so a typo never turns into configuration that does nothing.
- A duplicate key is an error (Python's `json` would silently keep the last one).
- Shas always complete (40 hex); repos always `owner/name`; globs never start with `/`.

<details>
<summary><b>settings</b> — <code>bugbot.json</code> and <code>bugbot.local.json</code>: global behavior of the bot</summary>

**Represents** the bot's defaults that apply to any repository: which owner's clones to offer (optional), where to look for clones, triage limits. It holds nothing from any project. `bugbot init [--org <name>]` creates `bugbot.json` with every setting at its initial value.

**Precedence**, strongest first: command flag → environment variable → `bugbot.local.json` → `bugbot.json` → the schema's `default` (only on keys whose absence has a meaning, such as `review.max_lows`: omitted = no limit). The initial value `bugbot init` writes is something else: each setting's `x-init`, the reference configuration. `.local` accepts any subset of the keys; lists are replaced whole.

**Structure**
```
github
  org                 string   optional owner (organization or user); empty or absent = any owner; init writes it only with --org   BUGBOT_ORG
  app                          GitHub App the bot acts as on one owner's repositories; off while id is 0
    owner             string   account the App is installed on; only its repositories use the App   BUGBOT_APP_OWNER
    id                integer  ≥ 0  the App's ID                                                    BUGBOT_APP_ID
    installation_id   integer  ≥ 0  the installation on owner                                        BUGBOT_APP_INSTALLATION_ID
    slug              string   reviews are posted, and recognized, as <slug>[bot]                    BUGBOT_APP_SLUG
    keychain_service  string   macOS Keychain service with the base64 private key                    BUGBOT_APP_KEYCHAIN_SERVICE
scan
  roots               string[] ≥ 1 item; ~ = home of whoever runs it  BUGBOT_SCAN_ROOTS
  depth               integer  1–8; counted to the clone's .git: 5 finds ~/a/b/c/repo  BUGBOT_SCAN_DEPTH
  prune               string[] directory names never walked          BUGBOT_SCAN_PRUNE
  inspect_jobs        integer  1–32                                 BUGBOT_INSPECT_JOBS
review
  author              string   @me | bot login: who posts the reviews and marks the rounds  BUGBOT_REVIEW_AUTHOR
  marker_version      integer  ≥ 1  version of the marker <!-- bugbot:vN sha= round= verdict= -->  BUGBOT_MARKER_VERSION
  language            string   BCP 47 (en, pt-BR, es): language of what is posted on the PR  BUGBOT_REVIEW_LANGUAGE
  parallel            integer  1–8  PRs reviewed at the same time (batch)      BUGBOT_REVIEW_PARALLEL
  delta_from_round    integer  ≥ 2  round from which only the delta is reviewed  BUGBOT_REVIEW_DELTA_FROM_ROUND
  max_rounds          integer  ≥ 1  rounds before stopping and suggesting a rethink  BUGBOT_REVIEW_MAX_ROUNDS
  max_lows            integer  ≥ 0  Lows posted per round, the rest goes to the audit; 0 or omitted = no limit  BUGBOT_REVIEW_MAX_LOWS
  max_prs             integer  1–50 PRs one run takes from the inbox (WIP) BUGBOT_REVIEW_MAX_PRS
retro
  auto_on_approve     boolean  runs and persists the retro after a published APPROVE  BUGBOT_RETRO_AUTO_ON_APPROVE
triage
  large_diff          integer  ≥ 1  production lines that raise one tier   BUGBOT_TRIAGE_LARGE_DIFF
  broad_change        integer  ≥ 0  production domains that raise one tier; 0 = off  BUGBOT_TRIAGE_BROAD_CHANGE
hooks                          whether the PR is reviewed, decided before the card
  draft               boolean  false = a draft PR is skipped          BUGBOT_HOOKS_DRAFT
  stale_days          integer ≥ 0  skips a PR without a commit for N days; 0 = never  BUGBOT_HOOKS_STALE_DAYS
  exclude_teams       string[] org/slug teams that do not count as yours  BUGBOT_HOOKS_EXCLUDE_TEAMS
  labels
    skip              string[] a PR with one of them is skipped       BUGBOT_HOOKS_SKIP_LABELS
    trigger           string[] non-empty = only review PRs with one of them BUGBOT_HOOKS_TRIGGER_LABELS
  reviewers
    max               integer ≥ 0  skips with N reviewers already; 0 = no limit  BUGBOT_HOOKS_REVIEWERS_MAX
    ignore            string[] logins that never count (service accounts)  BUGBOT_HOOKS_REVIEWERS_IGNORE
  ci                  passed|any  passed = only with required checks green  BUGBOT_HOOKS_CI
```

**Example** (`bugbot.json`, exactly what `bugbot init --org acme` writes; without `--org` the `github` group is left out; `doctor` checks it)
```json
{
  "$schema": "schemas/settings.schema.json",
  "github": { "org": "acme" },
  "scan": { "roots": ["~"], "depth": 5, "prune": ["node_modules", "Library", "build", ".Trash", "Pods", "DerivedData"], "inspect_jobs": 8 },
  "review": { "author": "@me", "marker_version": 1, "language": "en", "parallel": 2, "max_prs": 6, "delta_from_round": 3, "max_rounds": 3, "max_lows": 5 },
  "retro": { "auto_on_approve": true },
  "triage": { "large_diff": 1000, "broad_change": 3 },
  "hooks": { "draft": false, "stale_days": 30, "exclude_teams": [], "labels": { "skip": [], "trigger": [] }, "ci": "passed", "reviewers": { "max": 3, "ignore": [] } }
}
```

**Particulars**
- Each setting has, in the schema, `description`, `x-env` (the environment variable) and `x-init` (the value `bugbot init` writes). `lib/core/settings.py` only walks the schema; there is no other list of keys in the code. `bugbot config` shows value, origin and description.
- In the environment, a list is a JSON array (`BUGBOT_SCAN_ROOTS='["~/a", "/tmp/x y"]'`); a plain value becomes a one-item list and is never split on commas.
- Derived paths (`BUGBOT_HOME`, `BUGBOT_WORKSPACE_FILE`, `BUGBOT_DATA_DIR`, `BUGBOT_RETRO_DIR`) are not here: environment only. `BUGBOT_HOME` is never read by the CLI, which always uses its own location; it is how agents find the CLI.
- Without `bugbot.json`, every command except `init`, `doctor`, `format` and `validate` stops with `bugbot.json not found: run 'bugbot init'`.

**GitHub App** (`github.app`). Off by default: every call uses the person's `gh` login and reviews go out as them. With `id` set, the repositories of `owner` are read and written **only as the App**, and its reviews are posted as `<slug>[bot]`; repositories of other owners keep using the `gh` login.
- **Token:** `lib/core/app.py` signs a JWT with the App key and asks the installation for a token scoped to the repositories in use. The key comes from the macOS Keychain (`keychain_service`, stored base64-encoded) and goes from `security` to `openssl` through pipes: never on disk, in argv or in output. Tokens are cached with mode `0600` in `BUGBOT_DATA_DIR/state/app/` and reused while they have more than five minutes left.
- **No fallback:** the token goes into `GH_TOKEN` with `GH_CONFIG_DIR` pointing at an empty folder, so a failed mint stops the command instead of acting as the person.
- **Where:** `fetch`, `triage`, `publish` and `clean` switch to the App when the PR's repository belongs to `owner`; `inbox` searches those repositories with the App token. `setup` stays local and uses the `gh` login.
- **`review.author`:** `@me` means the App on its owner's repositories and the `gh` login elsewhere; any other value must be `<slug>[bot]`.
- **Inbox:** an App has no review requests, so for its repositories the inbox lists every open PR, narrowed by the hooks; `hooks.labels.trigger` is the opt-in gate (`doctor` warns when it is empty). `exclude_teams` needs the person's teams and is refused while `github.app` is set.
- **Setting it up:** create the App with `Pull requests: write`, `Contents: read`, `Checks: read` and `Commit statuses: read`, install it on the owner, then store the key once: `security add-generic-password -U -s <service> -a "$USER" -w "$(base64 -i key.pem)"` and delete the `.pem`. Rotating is the same command with a new key, then revoking the old one in the App's settings. `bugbot doctor` (group `environment`) signs a JWT, checks the App's slug and the installation's owner, mints a token for the workspace repositories and lists any missing permission.

**Hooks** (inspired by Renovate: `stopUpdatingLabel` → `labels.skip`, `rebaseLabel` → `labels.trigger`, `prCreation: status-success` / `ignoreTests` → `ci: passed | any`). One scalar key per behavior, no rule list. Evaluated in the order draft → stale_days → exclude_teams → labels.skip → labels.trigger → reviewers.max → ci; the first one that applies decides `review`, `skip` or `wait`.
- **Every hook applies to every tier.** The configuration is explicit: if `labels.skip` or `reviewers.max` skip a high PR, that is a risk taken by the configuration, not something the code silently undoes. (An earlier version exempted the high tier; it was removed for that reason.)
- Labels compared case-insensitively.
- `stale_days` (default 30) skips the PR whose **last commit** is N days old or older (committer date of the head): probably abandoned. A new commit brings it back. It measures the commit, not `updatedAt`, because a comment, a label or a bot also update the PR. `0` turns it off. In `triage` the value read goes to `hooks.last_commit_at`.
- `exclude_teams` skips the PR when **every** review request that reaches you comes from excluded teams. A request to you by name, or through another team of yours, keeps the PR. A PR with no request to you at all (opened by URL) is not affected: the filter only excludes. Default: empty (no team excluded). It reads the PR's `reviewRequests` and `gh api user/teams` (paginated), only when the list is not empty; failing to read the teams is an error, not silence. Teams as `org/slug`, as gh reports them, compared case-insensitively.
- `reviewers.max` can be set per repo in `repos/<owner>/<name>.json` (`hooks.reviewers.max`), which beats the global one.
- `reviewers.max` counts **distinct people** by the same criterion as GitHub's `reviewDecision`: for each person the last decisive review counts (`APPROVED`, `CHANGES_REQUESTED` or `DISMISSED`); a later `COMMENTED` does not undo the approval, a `DISMISSED` does. It counts if it is `APPROVED` or `CHANGES_REQUESTED`. These do not count: bots (`user.type: Bot` from the REST API — `gh pr view` does not report it), logins in `ignore`, the author, whoever runs the bot (the delta re-review cannot block itself) and whoever only commented. Reviews read via `gh api --paginate --slurp repos/{repo}/pulls/{n}/reviews`, only when `max > 0`.
- Validation beyond the schema (`cross_check` in `lib/core/settings.py`, applied by every command and shown by `doctor`): the same label in `skip` and `trigger` is an **error** (skip is evaluated first, so the PR would never be reviewed); the same label repeated with only a different case is a **warning**; `review.parallel` above `review.max_prs` is an **error**. A blank label, an exact duplicate, `ci` outside the enum and a non-boolean `draft` are already stopped by the schema.
- `ci: passed` only looks at **required** checks (`gh pr checks --required`): a failing optional check (e.g. an optional automated review) does not skip the review, it only raises a warning. Required with `fail`/`cancel` → skip; `pending` → wait. Repo without required checks (`no required checks reported`) → does not block. Unreadable checks (network error) → wait, never a silent review.
- `review.language` (default `en`) applies **only to what is posted on the PR**: inline comments and the review body. Everything else stays in English: CLI, logs, `candidates`, `verdicts`, `findings.json` and what the agents write, even when the PR, the code or `REVIEW.md` are in another language. The reason is that the English title is half of the fingerprint (file + title), and the same problem has to keep the same title in every round for "no repeats" to work. Translation happens when assembling what will be published, and the quoted part (code, rule) stays literal. BCP 47 format (`pt-BR`, not `ptBr`); a value outside the format is refused. Personal: use `bugbot.local.json` or `BUGBOT_REVIEW_LANGUAGE`.
- `mergeStateStatus` does **not** work: it stays `BLOCKED` while an approval is missing, that is, always before the review.
- Read live on every `triage` (labels, draft and checks change without a new commit). Skip or wait record no decision; `--force` goes ahead and records `forced: true`.

**Evolve:** a new setting = a property in the schema (type, limits, `description`, `x-env`, `x-init`) + a line in **Structure** and the example above updated (paste the output of `bugbot init --org acme --print`). Nothing in the code needs to list the key. `doctor` fails if an `x-init` (or `default`) is missing, if the example differs from `init` or if a command is missing from the **Commands** table.
</details>

<details>
<summary><b>workspace</b> — <code>workspace.json</code>: which repositories this machine reviews</summary>

**Represents** the map `repository → local canonical clone`. It is what lets `fetch` know where to fetch refs without cloning anything, and `setup` know which repos were already chosen.

**Personal:** it holds paths from your machine and your login. Each person generates their own with `bugbot setup`; it is in `.gitignore`.

**Structure**
```
version             1
github_user         string|null   gh login at the last setup (writes check the live login)
org                 string|null   owner setup filtered by; null = every owner was offered
updated_at          string        ISO 8601
repos               object        key = owner/name
  <owner/name>
    path*           string        canonical clone
    clone_url       string        normalized https; credentials are never recorded
    remote          string        the GitHub remote of the repo, preferably origin
    default_branch  string|null   from origin/HEAD, or from GitHub with --remote
    review_md       string|null   reserved: path of REVIEW.md
    enabled         boolean       false = keeps the entry, but commands refuse the repo
```
`*` = required.

**Example**
```json
{
  "version": 1,
  "github_user": "login-of-whoever-ran-it",
  "org": "acme",
  "repos": {
    "acme/web": {
      "path": "/Users/you/projects/web",
      "clone_url": "https://github.com/acme/web.git",
      "remote": "origin",
      "default_branch": "main",
      "review_md": null,
      "enabled": true
    }
  }
}
```

**Particulars:** running `setup` again updates `path`, `clone_url`, `remote` and `default_branch` and preserves the rest (manual edits included). A repo not found by the scan is kept; only what was offered in the selector and unchecked leaves. Atomic write.
</details>

<details>
<summary><b>repo</b> — <code>repos/&lt;owner&gt;/&lt;name&gt;.json</code>: review rules of one repository</summary>

**Represents** everything specific to a repository, always expressed with the **same keys**: that is what keeps the bot agnostic. It says which domain each file belongs to, which files raise the risk and which are harmless. Triage uses only this (and `meta.json`) to decide tier and scope.

**Life cycle:** `setup` creates the minimal file only if it does not exist → you add, at your own pace, the groups that make sense → `bugbot doctor` points out unconfigured groups, globs that match nothing and files outside any domain. The bot never rewrites an existing config.

**Format:** plain JSON, in the bot's code style (`bugbot format`). The explanation of each group lives in the schema (the editor shows it on hover) and here, not in the file.

**Structure**
```
$schema             string        schema pointer for the editor (stays at the root: that is where editors look)
$metadata*          object        about the file, not a review rule
  schema_version*   1             version of schemas/repo.schema.json the file follows
  created_at        string        ISO 8601, when setup created the file
repo*               string        owner/name the config belongs to
sensitive           array         files that take the PR to high with a deep floor; absent = no floor
  []
    glob*           glob
    reason*         string        shows on the triage card
    local_exec      allow|deny    deny = a PR touching the file runs no local check (default allow)
tests               glob[]        test files: outside risk, size and sensitive
ignore              glob[]        neither counted nor reviewed (generated output, vendored)
approve_deny        glob[]        never APPROVE a PR touching these: a clean review is posted as COMMENT
domains             array         ORDERED: the first match wins; absent = everything medium, scope by top-level folder (as a slug: `Modules` → `modules`, root → `root`)
  []
    name*           string        ^[a-z][a-z0-9-]{0,30}$
    risk            low|medium|high   default medium; the base tier is the highest risk touched
    paths*          glob[]
    description     string
large_diff          integer ≥ 1   overrides triage.large_diff from settings
hooks               object        overrides the global hooks only in this repo (key by key)
  reviewers
    max             integer ≥ 0   replaces hooks.reviewers.max from bugbot.json; 0 = no limit
```
Globs: `**` crosses directories, `*` and `?` stay within a segment, a trailing slash matches everything below; path from the repo root, never starting with `/`.

**Example** (as `setup` creates it, plus a calibrated `hooks`)
```json
{
  "$schema": "../../schemas/repo.schema.json",
  "$metadata": {
    "schema_version": 1,
    "created_at": "2026-11-12T15:10:00-03:00"
  },
  "repo": "acme/web",
  "hooks": {
    "reviewers": {
      "max": 3
    }
  }
}
```

**Example groups** (to copy when calibrating; illustrative values)
```json
"sensitive": [
  { "glob": ".github/workflows/**", "reason": "CI", "local_exec": "deny" }
],
"tests": ["**/__tests__/**", "**/*.test.*"],
"ignore": ["**/generated/**"],
"domains": [
  { "name": "docs", "risk": "low", "paths": ["**/*.md", "docs/**"] },
  { "name": "components", "risk": "high", "paths": ["packages/components/**"] }
]
```

**Particulars**
- `approve_deny` is for files whose change a person should approve however clean the review was: security boundaries (database and storage rules), the CI supply chain, build plugins and the instructions the reviewer is judged by. A `PASS` on a PR touching one goes out as `COMMENT`, with the status "LGTM, approval left to a person" and the file named; the verdict and the retro trigger stay as they are (no `APPROVE` was posted). `doctor` checks its globs against the clone like the other groups.
- `hooks` is **a choice, not a detection**: the generator never proposes it. Precedence of a hook: repo config > `bugbot.json` (which in turn may come from the environment). So `BUGBOT_HOOKS_REVIEWERS_MAX` changes the global default, but not the value a repo pinned. `triage.json` records in `hooks.config.overrides` where the value came from.
- `domains` is a list and not an object because order decides (a JSON object does not guarantee order). Tip: a `docs` domain first makes a README inside any folder count as docs; a `root` (`*`) last catches files at the root.
- Without a file, or with every group empty, the effect is the same: everything medium (high above `large_diff`), no floor, scope by top-level folder, and the card warns.
- A domain's `risk` and a file's `sensitive` play different roles: the domain says how risky the area **usually is** (suggests depth); `sensitive` says the file **always** needs full attention (floor). Putting a whole folder in `sensitive` makes the floor warning show up on every PR, and a constant warning becomes noise.
- `$schema` stays at the root and not inside `$metadata`: VS Code and IntelliJ only recognize it there.
- No comments in the file (plain JSON): the documentation of each group lives in the schema and in this README, and the file holds only decisions.

**Evolve:** a new per-repo rule = a new key in the schema (with `additionalProperties: false` a wrong key stays impossible to write) + reading it in `lib/core/repoconfig.py`. Useful generic globs go to the **Glob catalog** above (advisory), never into code.
</details>

<details>
<summary><b>meta</b> — <code>meta.json</code>: facts of a pull request</summary>

**Represents** what `fetch` found out about a PR at one moment: shas, files, lines, state. It is the input of triage and review. Facts only: title and description **never** enter a triage decision (PR text is data, not instruction); the finders read them only as the claim the change is checked against.

**Structure**
```
version*            1
repo*               string        owner/name
pr*                 integer ≥ 1
url, title, author  string|null
body                string|null   PR description, as the author wrote it (data)
state*              OPEN|CLOSED|MERGED
draft*              boolean
cross_repository    boolean       PR from a fork
head*               object
  sha*              sha
  ref               string|null   name of the author's branch
  local_ref*        string        refs/bugbot/<owner>/<name>/pull/<N>/head
base*               object
  branch*           string
  sha*              sha           base GitHub reports for the PR
  local_ref*        string
  fetched_sha*      sha           tip of the base branch when fetched (may be ahead)
merge_base*         sha           start of the three-dot range: the diff starts here
stats*              object        local files, additions, deletions
  github            object        the same numbers according to GitHub (for cross-checking)
files*              array
  []
    status*         A|M|D|R|C|T|U|X   git letter
    path*           string
    previous_path   string        on rename/copy
    additions, deletions  integer|null   null on binary
    binary          boolean
knowledge*          object        the repo's review instructions, located on the base (see below)
rounds*             object|null   bot rounds read from GitHub: author, count, last {id, head, round, verdict, at, commit}, commits_since, ignored_foreign
delta*              object|null   what changed in the PR itself since the head of the bot's last review; null without a previous review
  from_head, files[], lines, changes[{file, added, removed, state: new|dropped|changed}], patch, unavailable
  ref*              sha           base to read from: git show <ref>:<path>
  bytes*            integer       total size of the list (how much context it takes)
  files*            [{path, kind: review|project, covers, bytes, blob, symlink_from, also[]}]
  changed_by_pr*    string[]      instruction files the PR itself changes
paths*              object        clone, diff, files, findings, candidates, verdicts, worktree (null without --worktree)
                                  findings = where the review agent writes findings.json
fetched_at*         string
```

**Example**
```json
{
  "repo": "acme/web", "pr": 1234, "state": "OPEN", "draft": false,
  "head": { "sha": "60ebdaac…", "local_ref": "refs/bugbot/acme/web/pull/1234/head" },
  "base": { "branch": "main", "sha": "e6255d34…", "fetched_sha": "2317a8d9…" },
  "merge_base": "e6255d34…",
  "stats": { "files": 4, "additions": 80, "deletions": 4 },
  "files": [ { "status": "M", "path": "packages/cli/src/scaffold.ts", "additions": 11, "deletions": 3, "binary": false } ]
}
```

**Particulars**
- `base.sha` ≠ `base.fetched_sha` is normal (the base branch moved after the PR was opened). The diff uses `merge_base`, which is what GitHub shows.
- **`knowledge`**: `fetch` looks only for `AGENTS.md`, `CLAUDE.md` and `REVIEW.md` (exact name) at the root and in every parent directory of every changed file (and of a renamed file's old path), **at the tip of the base branch** (`fetched_sha`), never in the PR. **It only locates, it does not copy**: the agent reads each file with `git -C <clone> show <ref>:<path>`, so finders and verifiers see the same bytes whatever the checkout of the clone or the worktree, and the verifier can check a quoted rule. Reading from the worktree would give the PR's version; reading from the clone, the version of whatever branch you are on. Order: `REVIEW.md` before `AGENTS.md`/`CLAUDE.md`, from the most specific directory to the root. A symlink is resolved inside the repo (`path` is the final file, `symlink_from` the path that pointed to it) and listed once (`also`). Above 150 KB in total the card warns, because it is context every finder and verifier carries.
- Other formats (`.instructions.md`, `copilot-instructions.md`) are left out on purpose: they were written to generate code, not to review it. A team that wants one of them in the review includes it with an `@<file>` line in `REVIEW.md` (Claude Code Review's format).
- A PR that changes one of those files: the change is reviewed as code, but this round uses the base version, because a PR does not rewrite the rules it is judged by (same rule as Claude Code Review). The triage card warns (`review instructions changed by this PR`), and also when no `REVIEW.md` covers the changed files: that is the incentive for each area to write its own.
- Protection depends on the base: reading from the base only protects if changing the base requires review. An unprotected branch lets a new rule apply without review (a `doctor` check is open).
</details>

<details>
<summary><b>triage</b> — <code>triage.json</code>: risk, effort and your decision</summary>

**Represents** the result of a PR's triage (tier, suggested depth, scope per domain, local execution, CI) and the **history of your decisions per sha**. It is what the agent reads to decide which finders to dispatch and at what depth, and what the `retro` will read to calibrate triage (override rate).

**Structure**
```
version*            1
repo*, pr*, head*, author
config*             path, exists, configured          which repo config was used; configured = groups with entries
tier*               low|medium|high
reasons*            string[]                          why this tier (shows on the card)
depth*
  suggested*        quick|standard|deep
  floor*            quick|standard|deep|null          deep only when a `sensitive` file was touched
stats*              files, lines, production, tests, domains[], ignored, binary, large_diff, broad_change
                                                      binary = binaries outside `sensitive`, out of the review like `ignore`
                                                      production = lines outside low domains and tests (what large_diff measures)
scope*              { <domain>: { files, lines } }    largest first
local_exec*         allowed, denied_by[]              files that blocked local checks
ci*                 checks read live, in gh's buckets (no translation of our own)
  read*, error*     boolean, string|null              false = gh unreadable; hooks.ci passed becomes wait
  all*              pass, fail, cancel, skipping, pending, total, state, failing[]   every check
  required*         available + the same fields       only the required ones; available false = repo without any
                    state: fail|pending|pass|none     fail if any fail/cancel, else pending, else pass
hooks*              result of the bugbot.json hooks
  outcome*          review|skip|wait
  rule*             draft|stale_days|exclude_teams|labels.skip|labels.trigger|reviewers.max|ci|null
  reason*           string|null
  labels*, draft*   state read live
  reviewers*        string[]   people counted by reviewers.max (empty when max = 0)
  requested         string[]   review requests that reach you: your login and your teams (empty without exclude_teams)
  last_commit_at    string|null date of the head's last commit (what stale_days measures)
  config*           the hooks in force (a copy, so the record stays explainable)
round*              number, previous_head, scope, full_forced, commits_since, note
                                                      scope: full|delta|unchanged|capped (see Review rounds)
warnings*           string[]                          draft, CI failing, repo without config…
files*              []                                one per reviewable file
  path*, status*    string                            status = git letter (A, M, D, R…)
  lines*            integer                           additions + deletions
  binary*           boolean                           true only on a `sensitive` file (other binaries leave the list)
  domain*           string                            domain from the repo config
  risk*             low|medium|high                   risk of the file's domain
  test*             boolean                           matches `tests`
  sensitive*        [{glob, reason}]                  sensitive rules the file triggered (never on a test)
  local_exec*       allow|deny
rounds*             []  ≥ 1                           one per triaged sha, in order
  head*, at*
  agent             {outcome: keep|raise|lower, tier: {rules, final}, depth: {rules, final}, reason, at,
                     model, effort, session, run}   the last four: see Who ran each output
                                                      second opinion of the triage agent; absent = did not run on this head
  decision*         object below | null               null = no answer yet
decision            same as the last round's decision
  depth*            quick|standard|deep|abort
  suggested*        what was suggested (from the agent when it assessed the head, else from the rules)
  override*         boolean   you picked something other than the suggestion
  below_floor*      boolean   went below a high's floor
  forced            boolean   went ahead with --force although a hook said skip/wait
  at*
computed_at*        string
```

**Example** (illustrative, from a repo with a calibrated config)
```json
{
  "tier": "high",
  "reasons": ["high-risk domain: components", "sensitive: public API contract: breaking change"],
  "depth": { "suggested": "deep", "floor": "deep" },
  "stats": { "files": 9, "lines": 1194, "production": 348, "tests": 612, "domains": ["components"],
             "ignored": 1, "binary": 0, "large_diff": 1000, "broad_change": 0 },
  "scope": { "components": { "files": 7, "lines": 960 }, "docs": { "files": 2, "lines": 234 } },
  "local_exec": { "allowed": true, "denied_by": [] },
  "ci": {
    "read": true, "error": null,
    "all":      { "pass": 27, "fail": 0, "cancel": 0, "skipping": 2, "pending": 0, "total": 29, "state": "pass", "failing": [] },
    "required": { "available": true, "pass": 17, "fail": 0, "cancel": 0, "skipping": 0, "pending": 0, "total": 17, "state": "pass", "failing": [] }
  },
  "hooks": { "outcome": "review", "rule": null, "reason": null, "labels": [], "draft": false, "reviewers": [] },
  "rounds": [
    { "head": "a45eb8d5…", "decision": { "depth": "deep", "suggested": "deep", "override": false, "below_floor": false } }
  ]
}
```
1194 lines in the diff, but only 348 of production: 612 are tests and 234 documentation. What takes it to high is the domain (`components`, risk high) and a `sensitive` file (the public API snapshot), not the size.

**CI vocabulary:** the buckets are those of `gh pr checks --json bucket` (`cli/cli`, `pkg/cmd/pr/checks/aggregate.go`), untranslated: `pass` = SUCCESS; `fail` = ERROR, FAILURE, TIMED_OUT, ACTION_REQUIRED; `cancel` = CANCELLED; `skipping` = SKIPPED, NEUTRAL; `pending` = anything else (QUEUED, IN_PROGRESS, STALE…). `gh` also discards repeated runs of a check, so a re-run that passed hides the old failure.

**Particulars:** a new sha opens a new round (`note` says it is a delta re-review); answering again on the same sha replaces the decision. `rounds` is what makes triage calibratable: a frequent override in a repo points to a wrong rule in its config.
</details>

<details>
<summary><b>inbox</b> — <code>inbox.json</code>: PRs whose review was requested from you</summary>

**Represents** the result of a `bugbot inbox` search: the open PRs whose review was requested from you (by name or through a team of yours) in the workspace repos, already filtered by the hooks, and those left out with the reason. Disposable: written in the run's folder (`.bugbot/runs/<date>-inbox/`) and rebuilt on every call. `--json` prints the same content.

**Structure**
```
version*            1
query*              string     GitHub search built from the hooks and the workspace
me*                 string     login gh acts as
at*                 string     ISO 8601
searched*           integer    PRs the search returned, before the per-PR hooks
items*              item[]     to review: by repo (most recent activity first) and, within the repo, most recent activity first
hidden*             item[]     returned by the search and excluded, with hidden_by
item
  repo*, pr*, title*, url*, author*, head*, draft*, created_at*, updated_at*
  age_days*         integer    days since it was opened
  active_days*      integer    days since the last activity (updatedAt)
  last_commit_at*   string|null date of the head commit
  idle_days*        integer|null days since the head commit (what stale_days measures)
  requested*        string[]   requests that reach you: your login and your teams (org/slug)
  direct*           boolean    requested from you by name
  reviewers*        string[]   people counted by reviewers.max (same rule as triage)
  reviewers_max*    integer    the repo's effective reviewers.max (0 = no limit)
  ci*               pass|fail|pending|none|unread   required checks of the head; none = repo without a required check
  hidden_by         rule, reason            only in hidden; rule: stale_days|exclude_teams|reviewers.max|labels.skip|labels.trigger|draft|ci|decided|config
```

**Particulars**
- `title` is for display only, it never enters triage (same rule as `meta`).
- `ci` uses the same rule as `triage`: required checks, in gh's bucket vocabulary.
- `direct` (requested from you by name) stays only in the JSON, for consumers; it changes neither order nor display.

</details>

<details>
<summary><b>preflight</b> — <code>preflight.json</code>: how to prepare and check the PR locally</summary>

**Represents** the pre-flight plan of **one round** of a PR: whether something in the diff benefits from running, whether running is allowed and, when it is, the preparation and check steps the repository documents. The `triage` agent writes it (Preflight section), reading from the base; you and the report read it, and in the future the local checks. Nothing in it was executed. It lives in the PR's cache folder, next to `findings.json`, and `fetch` deletes it when the head changes: repo, PR and head come from the folder, not from the file.

**Structure**
```
version*            1
outcome*            none|blocked|proposed|unknown
reason*             one line: what changed and what the repo documents
sources*            [{path, ref}]   files read from the base to build the plan
steps*              []  empty outside proposed; in order: setup before checks
  id*               short name, unique in the plan
  purpose*          setup|build|test|lint|typecheck|other
  command*          as the repo documents it; only a target or path that covers no changed file may be dropped
  cwd*              relative to the worktree root; . for the root
  why*              which changed files the step exercises, or which step depends on it
  source*           {path, quote}   where the repo documents the command, quoted literally
  reuses*           string[]   caches outside the worktree reusable without changing the result
  cost*             low|medium|high
covers*             changed files the steps exercise
uncovered*          changed files that are compiled or executed and no step exercises (never prose, image or binary)
blocked_by*         changed files that block execution (local_exec or a step's source)
```

**Particulars**
- `proposed` only with a `source` on every step: a command without a quote from the repo is refused by the agent itself, and the gap becomes `unknown`.
- `blocked` covers both cases: triage's `local_exec: deny` and a step whose document, script or manifest the PR changes.
- `reuses` never points to dependencies installed in the clone: with the PR changing a dependency, the check would run against the clone's version.

</details>

<details>
<summary><b>candidates</b> — <code>candidates/&lt;finder&gt;.json</code>: what one finder found</summary>

**Represents** the output of **one** finder in one round: the problems it raised, not yet verified. Each finder writes its own file in `pr-N/candidates/` (created by `fetch`; path in `meta.json` → `paths.candidates`), so several run in parallel without fighting over a file. The verifiers receive one candidate at a time; the assembly numbers them (`id`), joins the verdicts (`verification`) and writes `findings.json`.

**Structure**
```
version*            2
candidates*         candidate[]   empty = nothing found
candidate           the finding of findings.schema.json without `id`, `domain`, `finder` and `verification`:
  severity* (Critical|High|Medium|Low), pre_existing*, category*, title*, explanation*,
  suggestion, file*, line*, side*, evidence* [{file, repo, ref, line, quote, note}], rule {source, quote}, confidence*
agent               {model*, effort*, session*, run}   written by the CLI, never by the finder (see Who ran each output)
```

**Particulars**
- **Judgment only.** What the CLI already knows is not written by the agent: the `finder` comes from the file name (`bugs-<domain>`, `security`, `convention`, `cross-domain`), each candidate's `domain` comes from `triage.json` by `file`, the covered domains come from the run's name, and the head is guaranteed by `fetch`, which empties the folder when the head changes. Fewer copied fields is less context and less chance of the agent miscopying.
- `evidence[].file` only accepts a file **from the repository** (no absolute path, `../` or `node_modules`): the verifier has to be able to open it. Behavior of a dependency or the platform goes in the `explanation`, and a finding that depends on it is `PLAUSIBLE`.
- `evidence[].repo`: another workspace repo (`owner/name`, a key of `workspace.json`), read from its clone on the default branch. It is the case of a doc that describes another repo's code: the finder quotes the code and the verifier checks it in the same place.
- `evidence[].ref`: `head` (default, read in the worktree) or `base` (code the PR removed or changed, read with `git show <merge_base>:<file>`).

</details>

<details>
<summary><b>verdicts</b> — <code>verdicts/&lt;finder&gt;.&lt;index&gt;.&lt;verifier&gt;.json</code>: one verdict</summary>

**Represents** the verdict of **one** verifier on **one** candidate (`candidates/<finder>.json`, position `index`; verifier `v1`, `v2` or `v3`), all of it said by the file name. One file per verdict, so verifiers run in parallel without fighting over a file. The assembly counts the verdicts and writes each finding's `verification` in `findings.json`.

**Structure**
```
version*            2
verdict*            keep|cut|unsure
reason*             one verifiable sentence: what was read and what it showed (in English)
checks*             the three questions, separately
  real*             yes|no|unsure      evidence matches, rule exists literally, explanation holds
  from_pr*          yes|pre_existing|no
  matters*          yes|no             defect vs style/cleanup/what CI covers
cut_reason          evidence_mismatch|rule_not_found|not_a_defect|unrelated_to_pr|style_only|unverified_intent|tool_noise|duplicate
severity            Critical|High|Medium|Low   the one the evidence supports; the assembly uses the highest among the `keep`s
duplicate_of        <finder>/<index>
agent               {model*, effort*, session*, run}   written by the CLI, never by the verifier
```

**Name** `<finder>.<index>.<verifier>.json`, dot-separated because no part has a dot:
- `finder`: the candidates file name without `.json` (`bugs-core`, `security`, `convention`), `^[a-z][a-z0-9-]{0,40}$`;
- `index`: position of the candidate in `candidates[]`, from 0;
- `verifier`: `v1`, `v2`, `v3`, in the order the orchestrator dispatches (`v3` only for the deep tie-break).

E.g. `bugs-core.0.v2.json` is the second verifier's verdict on the first candidate of `candidates/bugs-core.json`. The name format is in the schema (`x-file`, also in the candidates one): `validate` already refuses a name outside it, before the assembly, which also refuses an `index` that does not exist.

**Particulars**
- **Judgment only**, as in `candidates`. A verdict older than its candidates file is refused by the assembly: the finder ran again and the `index` may point to another candidate. This replaces the copied title and the head the verdict used to carry.
- The verdict follows from the checks: `keep` only with real `yes`, from_pr `yes`/`pre_existing` and matters `yes`; any `no` is `cut`; real `unsure` with the rest positive is `unsure`. Severity is assessed separately; if verifiers diverge, the assembly keeps the highest level one of them supported while keeping the finding.
- Dependency or platform: the verifier reads it where it is installed in the clone (the worktree has no installed dependencies); if it is not, or the PR changes its version, the step is `unsure`.

</details>

<details>
<summary><b>findings</b> — <code>findings.json</code>: what the review found</summary>

**Represents** the review result of **one round** of a PR (one head sha): every finding raised, the kept ones and those the verifiers cut. `bugbot findings` writes it, from the candidates and the verdicts (counting verdicts and assembling JSON is no job for a model); `publish` reads it. The CLI never invents or judges a finding. It lives in the PR's cache folder, next to `meta.json` and `triage.json`; the exact path is in `meta.json` → `paths.findings`.

**Full example**: in the schema itself, in `examples` (`schemas/findings.schema.json`) — High and Low findings and a cut one with the verifiers' reasons. The agent copies the shape; `schema.validate` rejects whatever strays.

**Structure**
```
version*            2
repo*, pr*          where from
head*               reviewed sha (meta.json head.sha); publish refuses it if it is not the current head
depth*              quick|standard|deep   triage.json's decision for that head
verdict*            PASS|REQUEST_CHANGES|COMMENT|SKIPPED|INVALID_INVOCATION
note                required in SKIPPED/INVALID_INVOCATION: why
round               {number, scope, previous_head, carried}   round and how many findings were carried
finders             [{name, domains[]}]   what ran and where; derived from the candidates file names
findings*           finding[]   all of them, kept and cut
finding
  id*               F1, F2...   stable reference within the file
  domain*           the file's domain (triage.json files[].domain; other outside it), filled by the assembly
  finder*           who raised it (candidates file name), filled by the assembly
  severity*         Critical|High|Medium|Low
  pre_existing*     boolean   already existed on the base; informs, never blocks
  category*         correctness|security|performance|api-contract|accessibility|convention|test|docs
  title*            one line, the problem (not the fix); part of the fingerprint, keep it stable across rounds
  explanation*      why it is a problem and what happens at runtime (Markdown; becomes the comment)
  suggestion        exact code for the anchored lines (GitHub suggestion block), or null
  file*, line*      where to anchor; a line outside the diff goes to "Outside the diff" in the body
  side*             RIGHT (PR version) | LEFT (removed line)
  evidence*         [{file, repo, ref: head|base, line, quote, note}]   ≥ 1; text copied from the code, not inferred from a name; from the repo or another workspace one (repo)
  rule              {source, quote}   required in practice for category convention
  confidence*       CONFIRMED|PLAUSIBLE
  carried_from      sha of the round the finding was carried from without a new verification
  verification*     {verdicts: [{verifier, verdict: keep|cut|unsure, reason}], outcome: keep|cut, duplicate_of}
```

**Particulars**
- **Absent ≠ empty.** A file that does not exist = the review did not run. An empty list or one with only `Low` findings results in `PASS`/`APPROVE`; `SKIPPED` with an empty list is "I did not look".
- **One file per round**, replaced in the next round of the same PR; `head` says which. History (fingerprint, severity, verdict, whether it was posted) goes to `retro/`, metadata only.
- **Report again in every round** every kept finding, even if a previous round already raised it: `publish` deduplicates by fingerprint (`file` + `title`, without the line, which changes on every rebase), and a finding missing from a round is how it knows the problem may have been resolved.
- **Cut ones stay in the file** with the reason: they show up only in the audit table, never as a comment. It is the only signal that a finder is inventing rules.
- `evidence` exists so the verifier can check without redoing the review; a claim based only on a function's name is not evidence.
- Severity per finding: `Critical`, `High`, `Medium` or `Low`; `pre_existing` says the PR did not introduce the problem. The repo's `REVIEW.md` can refine the criteria, not swap the scale.
- **Assembly** (`lib/findings/findings.py`), in order:
  1. only what belongs to the current head counts; a verdict from another head, or with a title different from the candidate's, is refused;
  2. verdicts per candidate: quick 1, standard 2, deep 2 (3 when the first two disagree); if a verdict is missing, the assembly stops (exit 2), unless `--partial`, which cuts the candidate as "not verified";
  3. **keep** only with a strict majority of `keep`; a tie and `unsure` count as a cut;
  4. final severity = the highest supported by any verifier that kept it; `pre_existing` if any of them said `pre_existing`;
  5. duplicate (same file and title, or a verdict's `duplicate_of`): only the first stays, the other goes to cut with `duplicate_of`;
  6. order: Critical, High, Medium, Low; within each level, kept before cut, new before pre-existing, then file and line; ids F1… in that order;
  6b. Low limit (`review.max_lows`, 0 or omitted = no limit): past the limit, the extra kept Lows go to cut with the reason `over review.max_lows`; carried ones (already posted) have priority. Critical, High and Medium are never cut by it;
  7. verdict: `REQUEST_CHANGES` with a new Critical/High; `COMMENT` with Medium or a pre-existing Critical/High; `PASS` only with Low or no findings.

</details>

<details>
<summary><b>retro-record</b> — JSONL event persisted by the retro skill</summary>

**Represents** a concise retro report of one round. `bugbot retro record` derives repo, PR,
head, round and verdict from the `findings.json` still in the cache and appends an event to
`BUGBOT_RETRO_DIR/<owner>/<repo>/pr-<N>.jsonl`.

**Structure**
```
version*, type*       1, retrospective
repo*, pr*, head*     identification of the PR and analyzed sha
round*, verdict*      round and verdict from findings.json
trigger*              auto_approve | manual
recorded_at*          timestamp of the recording
summary*              short summary of the retro
candidates*           [{category, priority: 1–5, summary, evidence[], destination, disposition}]
```

The record is append-only, validated by the schema, and holds no code excerpts or whole
threads. Automatic re-runs on the same head/round are idempotent.
</details>

<details>
<summary><b>translate</b> — <code>translate.json</code>: the review text in another language</summary>

**Represents** the published text of **one round** when `review.language` is not English. The `bugbot` agent writes it, from `findings.json`, before `publish`; `publish` reads it, picking it up on its own in the PR's folder (or via `--translation <file>`). Only what is posted is translated: `findings.json` stays in English, because the English title is part of the fingerprint and a language switch must not repost a finding.

**Structure**
```
version*            1
language*           review.language of this text (pt-BR, es...)
head*               head of findings.json; a translation of another round is refused
tldr                the review's summary paragraph, two sentences at most
findings            {F1: {title, explanation}, ...}   by findings.json id
text                fixed body texts, by key: heading, pass_tldr, unchanged_tldr,
                    findings_line, findings_line_first, scope_line, pre_existing, plausible,
                    rule, outside_title, outside_note, footer, approve_denied_status, approve_denied
```

**Particulars**
- The fixed texts of built-in languages (today `pt-BR`, in `LOCALES` of `lib/publish/publish.py`) come from the CLI: status, fixed lines and severity and category labels. The audit table and internal values (outcome, verdict, scope, depth) are bot logic and stay in English. The agent translates only `tldr` and `findings`; `text` is there to override. In a language without a built-in table, a missing key falls back to English.
- Code, identifiers, file paths and quoted rules are never translated (`rule.quote` goes as is).
- Placeholders in braces (`{open}`, `{round}`...) stay as they are in the `text` keys.
- With `review.language` other than English and no `translate.json`, `publish` warns: the fixed texts go out in the language (if built in), but summary and findings in English.

</details>

<details>
<summary><b>Who ran each output</b> — <code>agent</code>: model, effort and session</summary>

`candidates/*.json`, `verdicts/*.json` and `triage.json` (`rounds[].agent`) carry `agent: {model, effort, session, run}`: the model that **actually** ran, the reasoning effort (`null` when the harness does not record it; Copilot records the effective one, inherited from the session when the dispatch does not pass `reasoning_effort`), the id of the orchestrator's session in the harness (the log) and `run`, the id of the sub-agent itself in that log (`agentId` in Copilot and Claude Code), which separates runs that share the session. The CLI writes it, never the agent: `findings` on candidates and verdicts (without changing the mtime, which the order check uses) and `triage --assess` on triage. `preflight.json` comes from the same run as the triage. The retro compares `model` with the frontmatter's `model:`.

**How the CLI finds it.** The orchestrator gives each dispatch the name of the file it writes, `bugbot:<repo name>#<N>:<output>` (e.g. `bugbot:web#1234:verdicts/bugs-core.0.v2`), and `lib/core/harness.py` looks for that name in the harness logs since `fetched_at`; the last run counts. A session never goes into the file name: the name is the output's address (it links a verdict to a candidate, it is overwritten on a re-run), and a session is opaque, different per harness and changes on a retry.

| Harness | Log | Dispatch | Model and effort |
|---|---|---|---|
| Copilot (app and CLI) | `$COPILOT_HOME/session-state/<session>/events.jsonl` (default `~/.copilot`) | `subagent.started`: `agentDisplayName` or `agentDescription` | `subagent.configured` and `session.usage_record` of the `agentId` |
| Claude Code (CLI, Desktop, SDK) | `$CLAUDE_CONFIG_DIR/projects/<cwd>/<session>.jsonl` (default `~/.claude`) | `tool_use` `Task`/`Agent`: `input.description` | the sub-agent's `assistant` lines (`<session>/subagents/agent-<id>.jsonl`) |

Log not found or from another harness: the file is left without `agent`, nothing fails. The Claude Code format was tested with a synthetic transcript.

</details>

<details>
<summary><b>How to evolve a schema</b></summary>

1. **Compatible change** (a new optional key, a new enum value): edit the schema, adjust the code that reads it, keep `version: 1`. Old files stay valid.
2. **Incompatible change** (rename, remove, change type, make required): bump `version` in the schema (`"const": 2`), make the code accept both versions or migrate the old one, and record the decision here. In `repo` the version lives in `$metadata.schema_version`. Cache files (`meta`, `triage`) can simply be regenerated with `fetch` and `triage`.
3. **New JSON file:** create `schemas/<name>.schema.json`, validate with `schema.read_document(path, "<name>")` when reading and `schema.validate(data, "<name>")` before writing, write `$schema` with `schema.schema_ref(...)`, and add a section here.
4. **Keywords:** the validator (`lib/core/schema.py`) accepts only the subset in use: `type`, `enum`, `const`, `properties`, `required`, `additionalProperties`, `patternProperties`, `propertyNames`, `items`, `minItems`, `maxItems`, `uniqueItems`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, local `$ref`, `$defs`, `oneOf`, `anyOf` and the annotations (`description`, `default`, `x-env`, `x-file`, `x-init`…; `x-file` is the file name pattern, checked by `read_document`; `x-init` is the value `bugbot init` writes). Using another keyword fails when loading the schema, on purpose: extend the validator along with it.
</details>

## `bugbot inbox`

Lists the open PRs whose review was requested from you in the workspace repos, filtered by the hooks, to pick and send straight to `fetch` + `triage` (`lib/inbox/inbox.py`, `lib/commands/inbox.sh`).

```
bugbot inbox             selector (like setup's); the chosen ones go through fetch and triage, one by one
bugbot inbox --list      the list only (default without a terminal)
bugbot inbox --hidden    also shows what the hooks hid, and why
bugbot inbox --json      inbox.json on stdout
bugbot inbox --query     only the search string that was built
```

**With a GitHub App** (`github.app`), the App's repositories are searched apart, with its token and without `review-requested:@me` (an App has no review requests): every open PR there, narrowed by the hooks, with `labels.trigger` as the opt-in gate. `--query` shows both searches separated by ` | `.

**Who counts as "requested from you"**: GitHub's `review-requested:@me`, that is, requested by name **or** from any team of yours (CODEOWNERS or a manual request). Your own PRs never show up (`-author:@me`). In a monorepo most requests arrive through a team (CODEOWNERS): that is what `exclude_teams` is for.

**Hooks used in two places**, each where it is cheapest and exact:

| Hook | Where | How |
|---|---|---|
| `draft: false` | in the search | `draft:false` |
| `labels.skip` | in the search | `-label:"x"` for each |
| `labels.trigger` | in the search | `label:"a","b"` (any of them) |
| `stale_days` | in the search **and** per PR | `updated:>=<today − N>` cuts in the search (a safe lower bound: a PR with a recent commit was also updated on that date); the exact rule, by the head commit's date, runs per PR |
| `exclude_teams` | per PR | same function as triage (`requested_of_me` + `evaluate_hooks`) |
| `reviewers.max` | per PR | same function as triage (`count_reviewers`), with the repo's override |
| `ci` | per PR | same function as triage, with the head's **required checks** (`isRequired`, the same field as `gh pr checks --required`), last run per name. Read in a second GraphQL query only for the PRs that passed the other hooks (20 PRs per request). `ci: passed` hides a failing (skip) and a running (wait) required check |

A PR whose current head already has a decision recorded in the cached `triage.json` (`decided`) is also left out: it comes back when a new commit arrives.

**Cost**: one paginated GraphQL search (50 per page) brings, with each PR, the review requests and the reviews; the required checks come in a second query, in batches of 20 PRs. Plus `gh api user` and `gh api user/teams`. Rate limit cost: 1 point per page of 50 (limit 5000/h).

**Ordering and display**: the selector opens with only the repositories, from the most recent activity to the oldest, and each one expands with `→` (the same gesture as picking a clone in `setup`):
```
◆  PRs to review (0/12)
│  ❯ ▸ acme/web  5 PR(s)
│    ▾ acme/android  7 PR(s)  2 selected
│      ●  #1201  0d · Rev. 0/3 · CI PASS · Add retry to the upload screen
│      ○  #1207  0d · Rev. 0/3 · CI PASS · Guard navigation when the session expires
```
Within the repo, from the most recent activity to the oldest (`updatedAt`: commit, comment, review). PR line: days since the last activity · counted reviewers / `reviewers.max` · required checks (`CI PASS`, or `CI NOT REQUIRED` when the repo requires none) · title.

| Key | On a repository | On a PR |
|---|---|---|
| `→` | expands | — |
| `←` | collapses | collapses the group and goes back to the repo |
| `space` | checks/unchecks every PR of the repo | checks/unchecks the PR |
| `a` | checks/unchecks everything visible (a collapsed repo counts whole) | |
| `/` | searches every PR (number, title, repo name) and shows the groups with results already open; `esc` clears the search | |
| `enter` / `q` | confirms / quits | |

`--list` prints the same grouped list, without interaction.

**After selecting**, each PR goes through `bugbot fetch` and `bugbot triage`, the same commands you would run: card and decision are identical.

## `bugbot publish`

Publishes a PR's round as **one** review on GitHub (`lib/publish/publish.py` assembles, `lib/publish/gate.py` stops secrets, `lib/commands/publish.sh` checks, shows and posts). Status line, "Outside the diff", an audit with CUT, and a ledger in an HTML comment, fed by `findings.json`, the rounds and the language.

```
bugbot publish <pr> --dry-run     assembles and shows the review; posts nothing
bugbot publish <pr>               shows, asks [y/N] and posts on yes
bugbot publish <pr> --yes         posts without asking (the same checks run)
```

**Flow**, in this order; any failure stops before posting:
1. `meta.json` and `findings.json` exist; PR lock.
2. The PR is open and the current head is the head of `findings.json`. If the author pushed a commit, the round is redone (`fetch`, `triage`, review): a comment anchored on an old head would land on the wrong line.
3. The `gh` user is `review.author`.
4. Reads the bot's last review (its ledger) and the fingerprints of the inline comments that already exist on the PR.
5. Language: with `review.language` other than English, uses `translate.json` (or warns and posts in English).
6. Builds the payload and runs it through the secret gate.
7. Shows the preview and asks for confirmation; `--dry-run` stops here.
8. `POST repos/{repo}/pulls/{pr}/reviews` with the event derived from the verdict: `REQUEST_CHANGES` for a new Critical/High; `COMMENT` for Medium or a pre-existing Critical/High; `APPROVE` for only Low or no finding, unless the PR touches the repo's `approve_deny` (then `COMMENT`). The preview shows the event and it is only sent after explicit confirmation.
9. Minimizes the previous review as `OUTDATED` only when this round looked at nothing and neither did the previous one (`SKIPPED` or `INVALID_INVOCATION`). Inline threads are never minimized.

**Review body**
```
## Bugbot
#### 🔴 Request changes | 🟡 Comment | 🟢 Approved (LGTM) | ⚪ Not reviewed

**Findings:** 2 open (0 Critical, 1 High, 0 Medium, 1 Low) · 1 new · 1 already under discussion · 1 fixed since `abc123def`

<tldr: what the reader needs to know first (--tldr or translate.json); without findings: "Nothing to fix.">

**Review:** round 2 · delta · depth standard · 3 file(s), 40 line(s)

▸ Outside the diff — findings on lines the PR does not touch (GitHub does not accept inline there)
▸ Audit — N candidate(s), K kept, C cut   (table: id, severity, finding, outcome)

<sub>bugbot reviewed `<head>` against `<base>` · round `2`</sub>
<!-- bugbot:ledger {...} -->
<!-- bugbot:v1 sha=<head> round=2 verdict=REQUEST_CHANGES -->
```
The count comes before the summary, as in Claude Code's review. Only `Low` (or no finding) approves; `Medium` findings leave the review as `COMMENT`; new Critical/High request changes. An `unchanged` round goes out as `⚪ Not reviewed` with verdict `SKIPPED` in the marker and event `COMMENT`, even when carrying findings: that is what allows collapsing the next one without approving or blocking again.

**Inline comment**, one per new finding with a valid anchor (findings on the same line become a single comment, separated by `---`):
~~~
🟠 **High · correctness** — <title>

<explanation>

```suggestion
<code>
```
<sub>Rule: REVIEW.md — "<quote>"</sub>          only with rule
<sub>Likely, but not confirmed…</sub>            only with PLAUSIBLE
<!-- bugbot:f=<fingerprint> -->
~~~
Valid anchor = a line inside a hunk of `diff.patch` (`RIGHT`: added or context; `LEFT`: removed or context). An invalid anchor would make GitHub refuse the whole review, so the finding goes to "Outside the diff".

**Fingerprint** = `sha1(file + "\0" + normalized English title)[:12]`, without the line (it changes on every rebase). It goes in every inline comment and in the ledger.

**Ledger** (`<!-- bugbot:ledger {...} -->` in the body): every finding the PR ever had, with `fp`, file, line, severity, category, title, `status` (`open`/`fixed`), `first_sha`, `last_confirmed` and `fixed_at`. The next round reads the one from the bot's last review. Rules:
- **no repeats**: a finding with a thread on the PR (fingerprint in an inline comment) or open in the previous ledger is not commented again; it counts as "already under discussion";
- **fixed** only when an open finding disappeared from this round **and** its file changed since the previous round (`meta.delta`). A reviewer that just did not repeat itself does not close a finding nobody touched.

**Secret gate** (`gate.py scan`), over the raw payload and the decoded strings: private key, base64 key, JWT, Google API key, GitHub tokens, Anthropic, OpenAI and AWS keys, Slack token, and the values (12+ characters) of `.env*`, `local.properties` and `secrets.properties` not versioned in the clone. On a match: exit `3`, nothing is posted, and the right move is to fix the finding, not to work around the gate.

Exit: `0` posted (or dry run), `1` failure (network, gh, cancelled), `2` usage or precondition (PR closed, head changed, different author, no `findings.json`), `3` secret gate.

## `bugbot doctor`

**Read-only** health check (`lib/doctor/doctor.py`). It writes, fetches and changes nothing (`bugbot format` is what fixes formatting); the only network call is `gh api user`, which `--offline` skips. It runs even with broken settings or a missing tool, because that is exactly when it is needed: the dispatcher neither loads settings nor requires tools for this command.

| Group | Checks | Failure (■) | Warning (▲) |
|---|---|---|---|
| `format` | `bugbot.json`, `bugbot.local.json`, `workspace.json`, `schemas/*.json`, `repos/*/*.json` follow the code style | — | outside the code style (`run 'bugbot format'`); invalid JSON (the group that reads the file reports it) |
| `environment` | `python3` ≥ 3.9, `git`, `gh`, `zsh` and versions; `BUGBOT_HOME`; `gh` login; with `github.app`, the App key, App, installation and token permissions (skipped with `--offline`) | missing tool, `BUGBOT_HOME` pointing at another copy of the bot, `gh` not logged in; App key missing, wrong slug or owner, no `pull_requests: write` | old version; `BUGBOT_HOME` not set (agents fall back to `PATH`, or cannot find the CLI); `GH_TOKEN`/`GITHUB_TOKEN` in the environment overriding the login |
| `settings` | `init` can build a valid `bugbot.json` (every setting has an `x-init` or a `default`); `bugbot.json` and `bugbot.local.json` against the schema; then the **effective** values (files + environment) and the cross-field rules | a setting without an initial value; `bugbot.json` missing (`run 'bugbot init'`); invalid file; invalid value from the environment; the same label in `hooks.labels.skip` and `trigger` | no `$schema`; the same label with a different case |
| `schemas` | every schema loads with the validator | unsupported keyword, invalid JSON | does not declare Draft 2020-12 |
| `workspace` | `workspace.json` valid; each clone exists and its remote still points at the repo; each repo's default branch matches GitHub's (one GraphQL query for all; skipped with `--offline`) | clone gone, remote changed | no default branch, default branch different from GitHub's (`run 'bugbot setup'`), no `$schema`, comparison with GitHub not made |
| `repos` | each `repos/<owner>/<name>.json` valid and consistent with the clone | invalid (syntax errors with the line included), `repo` different from the file name | empty group (`not configured`), workspace repo without a config, orphan config, `.jsonc` file in `repos/` (not read), glob matching nothing (`sensitive`, `domains`, `tests`, `ignore` that no longer exist), file outside any domain (only if there are `domains`) |
| `cache` | `meta.json` and `triage.json` in `.bugbot/cache`; `cache/`, `worktrees/` and `state/locks/` hold only `<owner>/<name>/pr-<N>` | invalid file (regenerate with `fetch`/`triage` or `clean`) | entry outside the layout (older layout, loose file, empty folder), which `clean` cannot see (`purge`) |
| `docs` | each schema has a `<details>` in the Schemas section and every key/`x-env` appears in it; the `bugbot.json` example equals `init`'s; every `lib/commands/*.sh` has a row in the **Commands** table | schema without a block; example different from `init`; command without a row | undocumented key; block for a schema that does not exist |

Options: `--only <groups>` (comma-separated), `--quick` (does not compare configs with the clones, which is the slow part; the repo's line says `clone not checked (--quick)` so it does not look validated), `--offline` (does not query GitHub: neither login nor default branch), `--json` (list of `{group, status, check, detail}` on stdout). Exit `0` without failures (warnings allowed), `1` with a failure.

When to run it: after touching a schema, settings or a repo config (and `bugbot format` if it points at formatting); before sharing the bot; when a command fails in a strange way. The `docs` group is what keeps this README from falling behind the schemas and the commands.

## `bugbot format`

Rewrites the bot's own JSON files in the code style (`lib/format/format.py`, which `doctor` also uses, read-only). Files: `bugbot.json`, `bugbot.local.json`, `workspace.json`, `schemas/*.json`, `repos/*/*.json`. Nothing in `.bugbot/` (the bot already writes in the standard).

```
bugbot format            rewrites those outside the standard and lists each one
bugbot format --check    lists only; exit 1 if any needs formatting
```

Exit `1` also when there is an invalid file. It runs with broken settings, like `doctor` (a malformed `bugbot.json` is precisely one of the files it handles); it only requires `python3`.

**Code style** (`.editorconfig` at the root): UTF-8, LF, 2-space indentation (4 in `.py`), final newline, no trailing whitespace. For JSON, the canonical form is `json.dumps(indent=2)`: one value per line, no trailing comma. The editor applies `.editorconfig` while you type (IntelliJ/Android Studio natively; VS Code with the EditorConfig extension), and `format` guarantees the part the editor does not cover (structure). No Prettier: it would be Node + `package.json` just to format, and a second owner of the format.

How it rewrites safely:
- the new text is `json.dumps(indent=2)` of the data read, so the content does not change;
- atomic write (temporary file + `rename`), keeping the permissions.

## Structure

```
.editorconfig           code style (editor) — `bugbot format` applies the same to the bot's JSON files
bin/bugbot              dispatcher: resolves paths, loads the core, calls bb_cmd_<command>
lib/core/               shared between commands
  schema.py             JSON Schema validator (stdlib) and validated file reading
  harness.py            who ran each output: model, effort and session, read from the harness logs
  settings.py / .sh     settings driven by schemas/settings.schema.json → BUGBOT_*; initial bugbot.json for init
  workspace.py          reading, merging and writing workspace.json; lookup of a repo
  git.sh                bb_git (hardened git) and bb_github_slug
  gh.sh                 bb_gh and identity check
  lock.sh               per-PR lock
  pr.sh                 names per PR: paths and refs (bb_pr_names)
  repoconfig.py         loads a validated repos/<owner>/<name>.json and compiles the globs
  term.sh say.sh        terminal and the ┌ │ └ frame
  board.sh run.sh       step board ○ ◒ ● ▲ ■, log per step, spinner
  select.sh             selector (multiselect with sub-option) and confirmation
lib/commands/<name>.sh  one file per command, only composes steps
lib/<command>/          steps of one command, loaded only for it (setup/, fetch/, clean/, triage/, inbox/, findings/, publish/, retro/, doctor/, format/)
                        setup/repo_config.py: per-repo config template and check
                        publish/publish.py (assembles the review) and publish/gate.py (secret gate)
                        retro/record.py (validates and appends retro events)
                        a command that reuses another's steps sources them explicitly (purge → clean/)
.bugbot/                runs/ cache/ worktrees/ state/ (locks) — disposable
retro/                  permanent record of retrospectives per PR
repos/<owner>/<name>.json per-repo review config (domains, risk, sensitive, local execution) — personal
schemas/                format of every bot JSON: settings, workspace, repo, meta, triage, inbox, preflight, candidates, verdicts, findings, retro-record, translate
.gitignore              what is not versioned: personal data, generated settings, cache, calibration (see "What is yours and what is the bot's")
```

New command: `lib/commands/<name>.sh` with `bb_cmd_<name>` and `--help`; steps in `lib/<name>/*.sh`; a row in the **Commands** table (`doctor` checks it). Steps are functions run by `bb_run_step`, which writes `<run>/<key>.log` and accepts `bb_note` for the line's summary. Exit `10` in a step is success with a warning. Since each step runs in a subshell, the result returns to the command through files in `$BB_RUN_DIR`.

## Open items

- `REVIEW.md` (prose for the finders): format to be defined along with the finders. The data part went to `repos/<owner>/<name>.json`.
- `doctor` warning when a repo's base branch is not protected (the `knowledge` read from the base only protects if changing the base requires review).
- Which checks can run locally per repo, and with which definition (always the base's). **Pre-flight** already proposes the plan per PR from the repo's documentation; what is missing is executing the steps (with confirmation, only in the worktree, respecting `blocked_by`) and taking the result to the finders as evidence.
- The list of repos/contexts and the order in which per-domain reviewers are created. A second, different repo validates whether the model generalizes.
- PRs from forks: out of scope for now. `pull/N/head` should work, but it was never tested on a real fork PR.
- Preparing the worktree for checks (dependencies, toolchain): postponed. Attempt discarded, recorded so it is not repeated:
  - a symlink tree to the clone's `node_modules` with local copies of the packages the PR changes gives the wrong result: Node resolves a linked package's dependencies by its real path, so a linked lib keeps using the clone's version of a dependency the PR updated (false positive/negative when reviewing a bump). It is not a community pattern; pnpm and Yarn PnP work because they lay out the whole tree for it.
  - `npm ci --ignore-scripts --prefer-offline` with a warm cache in a large JS monorepo took tens of seconds and over a gigabyte, once per PR: the gain from shortcuts does not make up for the risk.
  - Direction if resumed: a setup command declared per repo, reviewed by the reviewer, with deterministic detection only as a configuration suggestion.
