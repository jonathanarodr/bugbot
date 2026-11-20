---
name: bugbot
description: >
  Review GitHub pull requests with the project's Bugbot workflow. Use when asked to review a PR,
  review pending requests, or review a repository.
---

# Bugbot review

Delegate the requested review to the existing `bugbot` agent defined in `$BUGBOT_HOME/.agents/bugbot.md`
(the bot can be cloned anywhere; `BUGBOT_HOME` says where). If `BUGBOT_HOME` is not set and `bugbot`
is not on `PATH`, ask the person where the bot is cloned instead of searching for it.
Pass the user's request and any PR or repository references through without changing their scope.

The `bugbot` agent owns the workflow, including running the CLI, dispatching specialist agents,
presenting findings, and asking before anything is published to GitHub. Do not run that workflow
yourself or publish a review directly.

If this harness cannot invoke the `bugbot` agent, explain that limitation and stop rather than
trying to perform the review in this skill's context.
