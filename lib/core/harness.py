#!/usr/bin/env python3
"""Which model, effort and session ran each sub-agent of a PR round, read from the harness's logs.

The orchestrator names every dispatch after the file it writes, `bugbot:<repo name>#<number>:<output>`
(.agents/skills/bugbot/SKILL.md); this finds that name in the logs and returns, per output, the last run's
{model, effort, session, run}: run is the sub-agent's own id, since sub-agents share the
orchestrator's session. Sessions never go into file names: the name is the output's address.

  copilot      $COPILOT_HOME/session-state/<session>/events.jsonl  (Copilot app and CLI; ~/.copilot)
  claude-code  $CLAUDE_CONFIG_DIR/projects/<cwd>/<session>.jsonl  (Claude Code CLI, Desktop, SDK; ~/.claude)

Another harness, or a log that cannot be read, gives no entry: callers leave the file as it is.
Standard library only, compatible with the macOS system Python (3.9).
"""

import datetime
import glob
import json
import os
import re


def _time(text):
    try:
        moment = datetime.datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _recent(pattern, since):
    cutoff = since.timestamp() if since else 0
    return [path for path in glob.glob(pattern) if os.path.getmtime(path) >= cutoff]


def _copilot(match, since, found):
    root = os.environ.get("COPILOT_HOME") or os.path.expanduser("~/.copilot")
    for path in _recent(os.path.join(root, "session-state", "*", "events.jsonl"), since):
        session, runs = os.path.basename(os.path.dirname(path)), {}
        for event in _lines(path):
            kind, data, agent_id = event.get("type"), event.get("data") or {}, event.get("agentId")
            when = _time(event.get("timestamp"))
            if kind == "session.start":
                session = data.get("sessionId") or session
            elif kind == "subagent.started" and when and (not since or when >= since):
                output = match(data.get("agentDisplayName")) or match(data.get("agentDescription"))
                if output:
                    runs[agent_id] = {"output": output, "at": when, "model": data.get("model"), "effort": None,
                                    "run": agent_id}
            elif kind == "subagent.configured" and agent_id in runs:
                runs[agent_id]["model"] = data.get("model") or runs[agent_id]["model"]
                runs[agent_id]["effort"] = data.get("reasoningEffort")
            elif kind == "session.usage_record" and agent_id in runs:
                usage = data.get("usage") or {}
                runs[agent_id]["model"] = usage.get("model") or runs[agent_id]["model"]
                runs[agent_id]["effort"] = usage.get("reasoningEffort") or runs[agent_id]["effort"]
        for run in runs.values():
            found.append(dict(run, session=session))


def _claude_code(match, since, found):
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for path in _recent(os.path.join(root, "projects", "*", "*.jsonl"), since):
        session = os.path.splitext(os.path.basename(path))[0]
        calls, agents = {}, {}
        for line in _lines(path):
            when = _time(line.get("timestamp"))
            content = (line.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in ("Task", "Agent"):
                    task = block.get("input") or {}
                    output = match(task.get("description")) or match(task.get("name"))
                    if output and when and (not since or when >= since):
                        calls[block.get("id")] = {"output": output, "at": when, "model": None,
                                                  "effort": line.get("effort")}
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    result = line.get("toolUseResult")
                    if isinstance(result, dict) and result.get("agentId"):
                        agents[result["agentId"]] = block["tool_use_id"]
                        calls[block["tool_use_id"]]["run"] = result["agentId"]
            if line.get("type") == "assistant" and line.get("agentId") in agents:
                run = calls[agents[line["agentId"]]]
                run["model"] = (line.get("message") or {}).get("model") or run["model"]
                run["effort"] = line.get("effort") or run["effort"]
        for agent_id, call_id in agents.items():
            for line in _lines(os.path.join(os.path.dirname(path), session, "subagents", f"agent-{agent_id}.jsonl")):
                if line.get("type") == "assistant":
                    calls[call_id]["model"] = (line.get("message") or {}).get("model") or calls[call_id]["model"]
                    calls[call_id]["effort"] = line.get("effort") or calls[call_id]["effort"]
        for run in calls.values():
            found.append(dict(run, session=session))


def runs(repo, number, since=None):
    """{output: {model, effort, session, run}} of the last run of each output dispatched since `since`."""
    pattern = re.compile(re.escape(f"bugbot:{repo.split('/')[-1]}#{number}:") + r"(\S+)", re.I)

    def match(text):
        hit = pattern.search(text) if isinstance(text, str) else None
        return re.sub(r"\.json$", "", hit.group(1)) if hit else None

    found = []
    for reader in (_copilot, _claude_code):
        try:
            reader(match, _time(since) if isinstance(since, str) else since, found)
        except Exception:  # a harness changed its log format: the others still count
            continue
    latest = {}
    for run in sorted(found, key=lambda run: run["at"]):
        if run["model"] and run["model"] != "<synthetic>":
            latest[run["output"]] = {"model": run["model"], "effort": run["effort"], "session": run["session"]}
            if run.get("run"):
                latest[run["output"]]["run"] = run["run"]
    return latest


def stamp(path, data, agent, write):
    """Put `agent` into a JSON file the bot read, keeping its mtime (the verdict/candidates order check uses it)."""
    if not agent or data.get("agent") == agent:
        return
    times = os.stat(path)
    data["agent"] = agent
    write(path, data)
    os.utime(path, (times.st_atime, times.st_mtime))
