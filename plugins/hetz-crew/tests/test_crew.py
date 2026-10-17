"""Static checks for the hetz-crew plugin (agents + /delegate), plus CLI checks when `claude` is on PATH.

Ground truth (Claude Code 2.1.272 bundle): plugin agents are named "<plugin>:<name>", accept effort in
low|medium|high|xhigh|max, maxTurns as a positive integer, and ignore permissionMode/hooks/mcpServers.
`claude plugin validate` does not check agent field values, so these tests do.
Set HETZ_NO_CLI=1 to skip the CLI checks.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "agents")
SKILL = os.path.join(ROOT, "skills", "delegate", "SKILL.md")
ROUTING = os.path.join(ROOT, "skills", "delegate", "references", "routing.md")

EXPECTED = {
    "runner": {"model": "haiku", "effort": "low", "maxTurns": 15, "color": "green"},
    "worker": {"model": "sonnet", "effort": "medium", "maxTurns": 40, "color": "blue"},
    "judge": {"model": "opus", "effort": "high", "maxTurns": 20, "color": "red"},
}
RETURN_MARKERS = {
    "runner": ["RESULT: pass|fail (exit N)", "FAILURES:", "NOTES:", "LOG:"],
    "worker": ["Changed:", "Check:", "Deviations:", "Open:"],
    "judge": ["VERDICT: FAIL", "VERDICT: PASS", "RISK:"],
}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
IGNORED_IN_PLUGIN_AGENTS = {"permissionMode", "hooks", "mcpServers", "initialPrompt"}
LISTING_BUDGET = 80  # always-on tokens, as reported by `claude plugin details`


def parse(path):
    """Minimal frontmatter parser: `key: value` scalars and `- item` lists. Returns (meta, body)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, f"{path}: missing frontmatter"
    meta, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key:
            if not isinstance(meta[key], list):
                meta[key] = []
            meta[key].append(item.group(1).strip())
            continue
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        assert kv, f"{path}: unparsable frontmatter line {line!r}"
        key, val = kv.group(1), kv.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta[key] = val
    return meta, m.group(2)


def tools(meta, key="tools"):
    val = meta.get(key, "")
    return {t.strip() for t in val.split(",") if t.strip()} if isinstance(val, str) else set(val)


def agent(name):
    return parse(os.path.join(AGENTS, name + ".md"))


def test_frontmatter_matches_yaml_when_available():
    yaml = pytest.importorskip("yaml")
    for path in [os.path.join(AGENTS, f) for f in os.listdir(AGENTS) if f.endswith(".md")] + [SKILL]:
        with open(path, encoding="utf-8") as f:
            fm = re.match(r"^---\n(.*?)\n---\n", f.read(), re.S).group(1)
        loaded = yaml.safe_load(fm)
        mine, _ = parse(path)
        assert {k: str(v).lower() if isinstance(v, bool) else (v if isinstance(v, list) else str(v)) for k, v in loaded.items()} == mine


def test_plugin_manifest():
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
        manifest = json.load(f)
    assert manifest["name"] == "hetz-crew"
    assert "version" not in manifest  # directory marketplace loads in place; no version bump needed


def test_exactly_three_agents():
    assert sorted(f for f in os.listdir(AGENTS) if f.endswith(".md")) == ["judge.md", "runner.md", "worker.md"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_agent_frontmatter(name):
    meta, _ = agent(name)
    assert meta["name"] == name and ":" not in name
    assert meta["description"]
    for key, want in EXPECTED[name].items():
        got = int(meta[key]) if key == "maxTurns" else meta[key]
        assert got == want, f"{name}.{key}: {got!r} != {want!r}"
    assert meta["effort"] in EFFORTS
    assert meta["color"] in COLORS
    assert int(meta["maxTurns"]) > 0
    assert not IGNORED_IN_PLUGIN_AGENTS & set(meta), "ignored for plugin agents"
    assert "omitClaudeMd" not in meta, "crew agents keep CLAUDE.md (project test commands live there)"


def test_tool_scopes():
    assert not WRITE_TOOLS & tools(agent("runner")[0])
    judge = agent("judge")[0]
    assert not WRITE_TOOLS & tools(judge)
    assert WRITE_TOOLS <= tools(judge, "disallowedTools")
    assert {"Edit", "Write", "Read", "Bash"} <= tools(agent("worker")[0])
    for name in EXPECTED:
        assert "Agent" not in tools(agent(name)[0]), "crew agents must not spawn agents"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_return_contract(name):
    _, body = agent(name)
    assert "RETURN" in body
    tail = body[body.index("RETURN"):]
    for marker in RETURN_MARKERS[name]:
        assert marker in tail, f"{name}: RETURN lacks {marker!r}"
    assert "nothing before or after" in tail


def test_descriptions_are_tiny_and_judge_is_gated():
    descs = {n: agent(n)[0]["description"] for n in EXPECTED}
    skill_desc = parse(SKILL)[0]["description"]
    # `claude plugin details` counts "name description" per agent and per skill (even disabled ones).
    assert sum(len(d) for d in descs.values()) + len(skill_desc) <= 300
    assert all(len(d) <= 110 for d in descs.values())
    assert "only if" in descs["judge"] and "user asks" in descs["judge"]
    assert descs["runner"].startswith("Haiku") and descs["worker"].startswith("Sonnet") and descs["judge"].startswith("Opus")
    assert "5k" in descs["runner"]


@pytest.mark.parametrize("name", ["runner", "worker"])
def test_command_logging_is_permission_friendly(name):
    """Bash's default timeout is 120 s (max 600000 ms); expansions in a redirect target defeat allow rules."""
    _, body = agent(name)
    assert "timeout 600000" in body
    commands = re.findall(r"`([^`]*)`", body)
    for banned in ("$(", "mktemp", "${TMPDIR", "$L"):
        assert not any(banned in c for c in commands), f"{name}: {banned!r} makes the redirect target runtime-determined"
    assert ">LOG 2>&1" in body and "literal path" in body


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_turn_budget_leaves_room_for_return(name):
    """At maxTurns the agent loop just breaks: no RETURN reaches the parent. Each agent must stop earlier."""
    meta, body = agent(name)
    m = re.search(r"After ~(\d+) tool turns, (?:stop and )?send RETURN", body)
    assert m, f"{name}: no explicit turn budget"
    assert int(m.group(1)) <= int(meta["maxTurns"]) - 5


def test_runner_reads_few_sources():
    assert "≤5 files" in agent("runner")[1]


def test_real_agent_listing_size():
    """The Agent tool lists `- <plugin>:<name>: <desc> (Tools: <tools minus disallowed>)`; plugin details
    counts only `name description`, so cap the real line size too (~90 tokens at chars/4)."""
    total = 0
    for name in EXPECTED:
        meta, _ = agent(name)
        allowed = [t for t in meta["tools"].split(", ") if t not in tools(meta, "disallowedTools")]
        total += len(f"- hetz-crew:{name}: {meta['description']} (Tools: {', '.join(allowed)})") + 1
    assert total <= 360, total


def test_agent_bodies_are_terse():
    for name in EXPECTED:
        _, body = agent(name)
        assert len(body) <= 1700, f"{name}: body {len(body)} chars"
        assert "<example>" not in body


def test_delegate_frontmatter():
    meta, _ = parse(SKILL)
    assert meta["name"] == "delegate"
    assert meta["disable-model-invocation"] == "true"
    assert meta.get("argument-hint")
    for banned in ("model", "effort", "context", "agent"):
        assert banned not in meta, f"/delegate must not set {banned} (never switch the main model)"
    allowed = meta["allowed-tools"]
    assert isinstance(allowed, list)
    agent_rule = next(a for a in allowed if a.startswith("Agent("))
    names = {n.strip() for n in agent_rule[len("Agent("):-1].split(",")}
    assert names == {"hetz-crew:runner", "hetz-crew:worker", "hetz-crew:judge", "Explore"}
    for n in names - {"Explore"}:
        assert os.path.exists(os.path.join(AGENTS, n.split(":", 1)[1] + ".md"))
    assert not any(a.split("(")[0] in WRITE_TOOLS for a in allowed)
    assert "Bash(git stash create)" in allowed  # diff base snapshot; touches no ref or file


def test_delegate_body_rules():
    _, body = parse(SKILL)
    assert "$ARGUMENTS" in body
    assert "/model" in body and "never" in body.lower()
    assert "5k" in body and "Never delegate" in body
    assert "Max 2 fix loops" in body
    # small diffs are reviewed inline, but never sensitive ones (must match routing.md)
    assert "except security/auth/data/migrations/deletes, which always get the judge" in body
    assert "≥30 lines or touches security/auth/data/migrations/deletes" in body
    # diff base excludes pre-existing work by content, not by path
    assert "git stash create" in body and "git diff <base> -- <files>" in body
    # acceptance commands go to the runner only when their output is bulky
    assert "runner if output may exceed ~5k tokens, else inline" in body
    assert "once per change" in body
    assert "run_in_background: false" in body
    assert "/hetz:handoff" in body and "/clear" in body
    for n in ("hetz-crew:runner", "hetz-crew:worker", "hetz-crew:judge"):
        assert n in body
    assert "${CLAUDE_SKILL_DIR}/references/routing.md" in body
    assert len(body) <= 2600, f"SKILL.md body {len(body)} chars (~650 tokens max)"


def test_routing_reference():
    with open(ROUTING, encoding="utf-8") as f:
        text = f.read()
    for needle in ("5k", "runner", "worker", "judge", "Explore", "/advisor", "regardless of size",
                   "no commits yet", "git diff <base> -- <files>"):
        assert needle in text
    assert "Pre-existing changes to ignore" not in text
    assert len(text) <= 3000


CLI = shutil.which("claude") and os.environ.get("HETZ_NO_CLI", "") in ("", "0")
TMP = os.environ.get("TMPDIR", "/tmp")


def _claude(*args):
    return subprocess.run(["claude", *args], capture_output=True, text=True, cwd=TMP, timeout=120)


@pytest.mark.skipif(not CLI, reason="claude CLI unavailable or HETZ_NO_CLI set")
def test_cli_validate():
    for path in (ROOT, os.path.join(ROOT, "agents"), os.path.join(ROOT, "skills")):
        r = _claude("plugin", "validate", path)
        assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not CLI, reason="claude CLI unavailable or HETZ_NO_CLI set")
def test_cli_listing_budget():
    r = _claude("--plugin-dir", ROOT, "plugin", "details", "hetz-crew")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"Agents \(3\)", r.stdout)
    m = re.search(r"Always-on:\s+~?([\d,]+)\s+tok", r.stdout)
    assert m, r.stdout
    assert int(m.group(1).replace(",", "")) <= LISTING_BUDGET, r.stdout
