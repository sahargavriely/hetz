"""hetz return-contract (SubagentStart): ask built-in Explore/general-purpose subagents for a terse return.

The parent re-reads a subagent's result on every later request, so a shorter result compounds.
Only the exact built-in agent types get it; Plan, crew and third-party agents keep their own formats.
A same-name user/project override (<scope>/.claude/agents/<type>.md, e.g. hetz's own Explore.md with its
15-line format) is a custom agent and keeps its own format too. HETZ_CONTRACT=0 disables it. Fails open.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hetzlib  # noqa: E402

AGENT_TYPES = ("Explore", "general-purpose")
CONTRACT = ("Return format for the parent, unless the task sets one: result first; evidence as path:line "
            "one-liners; at most 25 lines; no restating the task; no full file contents or raw logs; "
            "say what was not checked.")


def agent_dirs(cwd):
    """User agents dir, then each <dir>/.claude/agents from cwd up to (not including) home, like Claude Code."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "agents")]
    starts = [d for d in (cwd, os.environ.get("CLAUDE_PROJECT_DIR")) if isinstance(d, str) and os.path.isabs(d)]
    for start in starts:
        d = os.path.normpath(start)
        for _ in range(40):
            if d == home:
                break
            dirs.append(os.path.join(d, ".claude", "agents"))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return dirs


def overridden(agent_type, cwd):
    return any(os.path.isfile(os.path.join(d, agent_type + ".md")) for d in agent_dirs(cwd))


def main():
    if hetzlib.env_off("HETZ_CONTRACT"):
        return
    inp = hetzlib.read_input()
    agent_type = inp.get("agent_type")
    if inp.get("hook_event_name") != "SubagentStart" or agent_type not in AGENT_TYPES:
        return
    if overridden(agent_type, inp.get("cwd")):
        return
    hetzlib.emit({"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": CONTRACT}})


if __name__ == "__main__":
    hetzlib.run(main)
