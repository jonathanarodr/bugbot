---
name: tech-writer
description: >
  Review criteria for documentation changes: READMEs, guides, API docs, changelogs, code comments
  and instruction files. Loaded by the bugbot finders for the domains that declare it; holds what
  makes docs correct and useful, not a procedure to run. Use when a bugbot finder or verifier
  reviews files of a domain that lists this skill.
disable-model-invocation: true
metadata:
  author: bugbot
  version: "0.0.1"
  category: documentation
  tags: code-review,bugbot,documentation,docs,writing
---

# tech-writer

Review knowledge for documentation, used by the bugbot finders (`finder-bugs`,
`finder-convention`) and by the verifier that checks their candidates. Write **criteria**, not
steps: the finders only read files, so a step to build, render or publish docs is ignored.

Each rule is one sentence under a stable heading: `finder-convention` quotes it verbatim as the
`rule` of a candidate, and the verifier searches for that exact text. Say what is wrong, why it
misleads the reader, and the appropriate severity (`Critical`, `High`, `Medium` or `Low`). For docs, the failure that
matters most is that they lie: they describe code, commands or behavior that no longer exist.

## Critical and High

<!-- Docs that state something false and cause severe operational, security, privacy or data impact. -->

## Medium and Low

<!-- Medium: materially misleading and needs correction before approval. Low: unclear wording, missing example, stale link. -->

## Do not flag

<!-- Style a linter or formatter already enforces, or tone preferences with no written rule. -->

## References

<!-- Longer material lives in references/<topic>.md, read only when the change touches it:
| Change touches | Read |
|---|---|
| ... | [Topic](references/topic.md) |
-->
