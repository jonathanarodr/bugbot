---
name: devops-reviewer
description: >
  Review criteria for build, CI/CD and infrastructure: pipelines, workflows, containers,
  infrastructure as code, release and deploy scripts. Loaded by the bugbot finders for the domains
  that declare it; holds what is correct and what is a problem in this stack, not a procedure to
  run. Use when a bugbot finder or verifier reviews files of a domain that lists this skill.
disable-model-invocation: true
metadata:
  author: bugbot
  version: "0.0.1"
  category: development
  tags: code-review,bugbot,devops,ci,cd,infrastructure,pipeline
---

# devops-reviewer

Review knowledge for DevOps code, used by the bugbot finders (`finder-bugs`,
`finder-convention`) and by the verifier that checks their candidates. Write **criteria**, not
steps: the finders only read code, so a step to run a build, a test or a command is ignored.

Each rule is one sentence under a stable heading: `finder-convention` quotes it verbatim as the
`rule` of a candidate, and the verifier searches for that exact text. Say what is wrong, why it
breaks at runtime, and the appropriate severity (`Critical`, `High`, `Medium` or `Low`).

## Critical and High

<!-- Severe or catastrophic behavior, security, privacy or data-integrity failures in this stack. -->

## Medium and Low

<!-- Medium: actionable defect needing a fix before approval. Low: non-blocking, approve with comment. -->

## Do not flag

<!-- What this stack's tooling already enforces (linters, compilers, formatters) or what is
     acceptable here although it looks wrong elsewhere. -->

## References

<!-- Longer material lives in references/<topic>.md, read only when the change touches it:
| Change touches | Read |
|---|---|
| ... | [Topic](references/topic.md) |
-->
