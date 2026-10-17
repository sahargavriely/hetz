#!/usr/bin/env python3
"""hetz A/B bench runner: arms x tasks x k repetitions of `claude -p`, in randomized order.

Dry-run by default (prints the plan and every command); spends tokens only with --execute.
Results go to bench/results/<run_id>.json; summarize them with bench/report.py.
Python 3 stdlib only.
"""
import argparse
import copy
import datetime as dt
import glob
import json
import os
import platform
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import uuid

BENCH = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BENCH)
ARMS_DIR = os.path.join(BENCH, "arms")
TASKS_DIR = os.path.join(BENCH, "tasks")
RESULTS_DIR = os.path.join(BENCH, "results")

TURN_SEP = "<!-- turn -->"
HANDOFF_PROMPT = "/hetz:handoff"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
DEFAULT_BUDGET = 3.0
EXPECT_VERSION = "2.1.272"
ALLOWED_TOOLS = "Bash,Edit,Write"
PRESETS = {
    # design: the cheap pair (A vs D on tasks 3, 4, 7) runs on every change
    "cheap": {"arms": ["A", "D"], "tasks": ["repo-qa", "noisy-suite", "three-tasks"]},
}
# Inherited env is dropped when it would leak the caller's Claude session (nested runs inherit host-auth
# and session vars) or pre-set a lever an arm controls. Every CLAUDE_CODE_* var goes except auth/provider/
# network ones (KEEP_ENV), so `--settings` and the arm env are the only levers in play.
STRIP_ENV = {
    "CLAUDECODE", "CLAUDE_ENV_FILE", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR",
    "CLAUDE_PID", "CLAUDE_EFFORT", "MAX_MCP_OUTPUT_TOKENS", "BASH_MAX_OUTPUT_LENGTH", "MAX_THINKING_TOKENS",
    "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "NO_COLOR", "FORCE_COLOR", "PYTEST_ADDOPTS", "CI",
}
# ANTHROPIC_DEFAULT_*_MODEL / ANTHROPIC_CUSTOM_MODEL_OPTION remap the opus/sonnet/haiku aliases that arms,
# the Explore override, the crew agents and CLAUDE_CODE_SUBAGENT_MODEL rely on.
STRIP_ENV_PREFIXES = ("HETZ_", "CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "ANTHROPIC_DEFAULT_", "ANTHROPIC_CUSTOM_MODEL_OPTION")
KEEP_ENV = re.compile(r"CLAUDE_CODE_(USE_|SKIP_\w*AUTH|OAUTH_TOKEN|OAUTH_REFRESH_TOKEN|API_KEY_|API_BASE_URL|"
                      r"CLIENT_|CERT_STORE|PROXY_|HTTPS?_PROXY|GATEWAY_|CUSTOM_OAUTH_URL)")
RUN_ENV = {"DISABLE_AUTOUPDATER": "1"}  # keep the pinned Claude Code version for the whole run
CACHE_BREAK_TOKENS = 20000
# Every fixture path contains this, so ~/.claude/projects/<encoded cwd> names it too: the field ledger
# (scripts/hetz_ledger.py) must skip such project dirs, or bench spend pollutes the real-work numbers.
BENCH_MARKER = "hetz-bench"
TOKEN_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


class BenchError(Exception):
    pass


# ---------------------------------------------------------------- arms

def deep_merge(base, over):
    """Recursive dict merge; lists and scalars in `over` replace; a None value deletes the key."""
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def remove_path(obj, dotted):
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return
    if isinstance(obj, dict):
        obj.pop(parts[-1], None)


def load_arm_specs(arms_dir=ARMS_DIR):
    specs = {}
    for path in sorted(glob.glob(os.path.join(arms_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            specs[os.path.splitext(os.path.basename(path))[0]] = json.load(f)
    return specs


def _settings_file(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}  # "_comment"-style keys are not settings


def _union_merge(base, over):
    """Profile merge rule (profiles/*.json _doc): dicts merge, arrays union, scalars replace."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _union_merge(out[key], value)
        elif isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = out[key] + [v for v in value if v not in out[key]]
        else:
            out[key] = copy.deepcopy(value)
    return out


def _fill(obj, values, where):
    if isinstance(obj, dict):
        return {k: _fill(v, values, where) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values, where) for v in obj]
    if isinstance(obj, str):
        for name, value in values.items():
            obj = obj.replace("{" + name + "}", str(value))
        left = re.findall(r"\{[a-z_]+\}", obj)
        if left:
            raise BenchError(f"{where}: unfilled placeholder {left[0]} (add it to settings_profile.fill)")
    return obj


def profile_settings(spec, where):
    """Settings from a hetz profile ({"items": {name: {"settings": ...}}}), minus excluded items."""
    path = os.path.join(REPO, spec["path"])
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)["items"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BenchError(f"{where}: cannot read profile {spec['path']}: {e}")
    unknown = set(spec.get("exclude", [])) - set(items)
    if unknown:
        raise BenchError(f"{where}: profile has no item(s) {sorted(unknown)}")
    out = {}
    for name, item in items.items():
        if name not in spec.get("exclude", []):
            out = _union_merge(out, item.get("settings") or {})
    return _fill(out, spec.get("fill", {}), where)


def resolve_arm(name, specs, _stack=()):
    """Flatten an arm spec through its `extends` chain (see README for the fields)."""
    if name not in specs:
        raise BenchError(f"unknown arm {name!r} (have: {', '.join(sorted(specs))})")
    if name in _stack:
        raise BenchError(f"arm extends cycle: {' -> '.join(_stack + (name,))}")
    spec = specs[name]
    if spec.get("extends"):
        base = resolve_arm(spec["extends"], specs, _stack + (name,))
    else:
        base = {"settings": {}, "plugin_dirs": [], "env": {}, "project_files": {}, "model": None,
                "effort": None, "rotate": "resume", "extra_args": [], "prompt_prefix": "", "extra_turns": []}
    settings = base["settings"]
    if spec.get("settings_profile"):
        settings = deep_merge(settings, profile_settings(spec["settings_profile"], f"arm {name}"))
    for rel in spec.get("settings_files", []):
        settings = deep_merge(settings, _settings_file(rel))
    settings = deep_merge(settings, spec.get("settings"))
    for dotted in spec.get("remove_settings", []):
        remove_path(settings, dotted)
    rotate = spec.get("rotate", base["rotate"])
    if rotate not in ("resume", "clear", "handoff"):
        raise BenchError(f"arm {name}: rotate must be resume|clear|handoff, not {rotate!r}")
    extra_turns = list(spec.get("extra_turns", base["extra_turns"]))
    if any(not isinstance(t, str) or not t.strip() or t.lstrip().startswith("-") for t in extra_turns):
        raise BenchError(f"arm {name}: extra_turns must be non-empty strings not starting with '-'")
    return {
        "name": name,
        "description": spec.get("description", ""),
        "extends": spec.get("extends"),
        "settings": settings,
        "plugin_dirs": list(spec.get("plugin_dirs", base["plugin_dirs"])),
        "env": {k: str(v) for k, v in deep_merge(base["env"], spec.get("env")).items()},
        "project_files": deep_merge(base["project_files"], spec.get("project_files")),
        "model": spec.get("model", base["model"]),
        "effort": spec.get("effort", base["effort"]),
        "rotate": rotate,
        "extra_args": list(spec.get("extra_args", base["extra_args"])),
        "prompt_prefix": spec.get("prompt_prefix", base["prompt_prefix"]),
        "extra_turns": extra_turns,
        # not inherited: these describe this arm's role in the matrix
        "only_tasks": spec.get("only_tasks"),
        "ablates": spec.get("ablates"),
        "ablation_base": spec.get("ablation_base", "D" if spec.get("ablates") else None),
        "target_tasks": spec.get("target_tasks"),
        "variant_of": spec.get("variant_of"),
    }


# ---------------------------------------------------------------- tasks

def list_tasks(tasks_dir=TASKS_DIR):
    return sorted(d for d in os.listdir(tasks_dir) if os.path.isfile(os.path.join(tasks_dir, d, "task.md")))


def load_task(name, tasks_dir=TASKS_DIR):
    tdir = os.path.join(tasks_dir, name)
    if not os.path.isfile(os.path.join(tdir, "task.md")):
        raise BenchError(f"unknown task {name!r} (have: {', '.join(list_tasks(tasks_dir))})")
    with open(os.path.join(tdir, "task.md"), encoding="utf-8") as f:
        text = f.read()
    turns = [t.strip() for t in text.split(TURN_SEP) if t.strip()]
    if not turns or any(t.startswith("-") for t in turns):
        raise BenchError(f"task {name}: empty turn list, or a turn starts with '-' (would parse as a flag)")
    try:
        with open(os.path.join(tdir, "max_turns"), encoding="utf-8") as f:
            max_turns = int(f.read().strip())
    except (OSError, ValueError) as e:
        raise BenchError(f"task {name}: bad max_turns ({e})")
    for script in ("setup.sh", "check.sh"):
        if not os.path.isfile(os.path.join(tdir, script)):
            raise BenchError(f"task {name}: missing {script}")
    return {"name": name, "dir": tdir, "task_md": os.path.join(tdir, "task.md"), "turns": turns,
            "max_turns": max_turns, "setup": os.path.join(tdir, "setup.sh"),
            "check": os.path.join(tdir, "check.sh")}


# ---------------------------------------------------------------- plan

def build_schedule(arms, tasks, k, warmup, seed):
    """All (arm, task, rep) runs in one seeded shuffle; the first `warmup` runs executed per cell are
    flagged warm-up (report.py drops them)."""
    runs = []
    for arm in arms:
        for task in tasks:
            if arm["only_tasks"] and task["name"] not in arm["only_tasks"]:
                continue
            runs += [{"arm": arm["name"], "task": task["name"]} for _ in range(k + warmup)]
    random.Random(seed).shuffle(runs)
    seen = {}
    for order, run in enumerate(runs):
        cell = (run["arm"], run["task"])
        rep = seen.get(cell, 0)
        seen[cell] = rep + 1
        run.update(order=order, rep=rep, warmup=rep < warmup)
    return runs


def arm_turns(arm, task):
    """The task's turns as this arm sends them: prompt_prefix on the first, extra_turns appended."""
    turns = list(task["turns"])
    turns[0] = arm["prompt_prefix"] + turns[0]
    return turns + arm["extra_turns"]


def plan_invocations(arm, task):
    """[(kind, turn_index, prompt, session_mode, session_key)] for one run. session_mode is 'new' or
    'resume'; session_key names the session so callers can map it to a UUID."""
    turns = arm_turns(arm, task)
    if len(turns) == 1 or arm["rotate"] == "resume":
        return [("turn", i, p, "new" if i == 0 else "resume", 0) for i, p in enumerate(turns)]
    out = []
    for i, prompt in enumerate(turns):
        if i and arm["rotate"] == "handoff":
            out.append(("handoff", i - 1, HANDOFF_PROMPT, "resume", i - 1))
        out.append(("turn", i, prompt, "new", i))
    return out


def claude_argv(claude, prompt, arm, task, settings_path, opts, session_flag):
    argv = [claude, "-p", prompt,
            "--output-format", "json",
            "--setting-sources", "project,local",
            "--settings", settings_path,
            "--strict-mcp-config"]
    for d in arm["plugin_dirs"]:
        argv += ["--plugin-dir", os.path.join(REPO, d)]
    argv += ["--model", arm["model"] or opts.model,
             "--effort", arm["effort"] or opts.effort,
             "--max-turns", str(task["max_turns"]),
             "--max-budget-usd", f"{opts.budget_usd:g}",
             "--permission-mode", "acceptEdits",
             "--permission-prompts", "none",
             "--allowedTools", ALLOWED_TOOLS]
    argv += session_flag
    argv += arm["extra_args"]
    return argv


def clean_env(arm, base=None):
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if not _stripped(k)}
    env.update(RUN_ENV)
    env.update(arm["env"])
    return env


def stripped_env_names(base=None):
    base = os.environ if base is None else base
    return sorted(k for k in base if _stripped(k))


def _stripped(name):
    return name in STRIP_ENV or (name.startswith(STRIP_ENV_PREFIXES) and not KEEP_ENV.match(name))


# ---------------------------------------------------------------- dry run

def print_plan(arms, tasks, schedule, opts, run_id, work_root, out=sys.stdout):
    def say(s=""):
        out.write(s + "\n")

    n_measured = sum(not r["warmup"] for r in schedule)
    say(f"# hetz bench DRY RUN (nothing executed; add --execute to spend tokens) run_id={run_id}")
    say(f"# model={opts.model} effort={opts.effort} k={opts.k} warmup={opts.warmup} seed={opts.seed} "
        f"budget/invocation=${opts.budget_usd:g}")
    say(f"# {len(schedule)} runs ({n_measured} measured + {len(schedule) - n_measured} warm-up); "
        f"worst-case spend <= ${worst_case(schedule, arms, tasks, opts):.2f}")
    say(f"# results -> {os.path.join(opts.out, run_id + '.json')}")
    say("# each claude call drops inherited HETZ_*, CLAUDE_CODE_* (except auth/provider), model-alias and "
        "lever env vars")
    for arm in arms:
        say(f"# arm {arm['name']}: {arm['description']}")
        if arm["prompt_prefix"] or arm["extra_turns"]:
            say(f"#   prompt prefix {arm['prompt_prefix']!r}, extra turns {arm['extra_turns']!r}")
        say(f"#   settings {settings_path_for(work_root, arm)} = {json.dumps(arm['settings'], sort_keys=True)}")
        if arm["env"]:
            say(f"#   env {' '.join(f'{k}={v}' for k, v in sorted(arm['env'].items()))}")
        for dest, src in sorted(arm["project_files"].items()):
            missing = "" if os.path.exists(os.path.join(REPO, src)) else "  (MISSING)"
            say(f"#   project file {dest} <- {src}{missing}")
    for task in tasks:
        say(f"# task {task['name']}: {len(task['turns'])} turn(s), max_turns={task['max_turns']}")
    say()
    for run in schedule:
        arm = next(a for a in arms if a["name"] == run["arm"])
        task = next(t for t in tasks if t["name"] == run["task"])
        wdir = run_dir(work_root, run)
        repo = os.path.join(wdir, "repo")
        tag = " (warm-up)" if run["warmup"] else ""
        say(f"# [{run['order'] + 1}/{len(schedule)}] arm={arm['name']} task={task['name']} rep={run['rep']}{tag}")
        say(f"sh {shlex.quote(task['setup'])} {shlex.quote(repo)}")
        for dest, src in sorted(arm["project_files"].items()):
            say(f"mkdir -p {shlex.quote(os.path.dirname(os.path.join(repo, dest)))} && "
                f"cp {shlex.quote(os.path.join(REPO, src))} {shlex.quote(os.path.join(repo, dest))}")
        sessions = {}
        for kind, idx, prompt, mode, key in plan_invocations(arm, task):
            sid = sessions.setdefault(key, str(uuid.uuid4()))
            flag = ["--session-id", sid] if mode == "new" else ["--resume", sid]
            argv = claude_argv("claude", prompt, arm, task, settings_path_for(work_root, arm), opts, flag)
            if arm_turns(arm, task) == task["turns"] and len(task["turns"]) == 1:
                shown = " ".join([argv[0], argv[1], f'"$(cat {shlex.quote(task["task_md"])})"'] +
                                 [shlex.quote(a) for a in argv[3:]])
            else:
                shown = shlex.join(argv)
            env = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted({**RUN_ENV, **arm["env"]}.items()))
            say(f"(cd {shlex.quote(repo)} && env {env} {shown})")
        say(f"sh {shlex.quote(task['check'])} {shlex.quote(repo)} {shlex.quote(os.path.join(wdir, 'answer.txt'))}")
        say()


def worst_case(schedule, arms, tasks, opts):
    by_arm = {a["name"]: a for a in arms}
    by_task = {t["name"]: t for t in tasks}
    return sum(len(plan_invocations(by_arm[r["arm"]], by_task[r["task"]])) * opts.budget_usd for r in schedule)


def settings_path_for(work_root, arm):
    return os.path.join(work_root, "settings", f"{arm['name']}.json")


def run_dir(work_root, run):
    return os.path.join(work_root, f"{run['order']:04d}-{run['arm']}-{run['task']}-r{run['rep']}")


# ---------------------------------------------------------------- execute

def projects_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def transcript_stats(session_ids):
    """Peak/median context, request and cache-break counts from the main transcripts (read-only),
    subagent request counts, and token sums over main + subagent transcripts (a cross-check of the
    result's modelUsage). None when no transcript is found."""
    contexts, breaks, sub_requests, found = [], 0, 0, False
    tokens = {k: 0 for k in TOKEN_KEYS}
    for sid in session_ids:
        paths = glob.glob(os.path.join(projects_dir(), "*", sid + ".jsonl"))
        if not paths:
            continue
        found = True
        usages = _usages(paths[0])
        for i, u in enumerate(usages):
            contexts.append(sum(int(u.get(k) or 0) for k in
                                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
            if i and int(u.get("cache_creation_input_tokens") or 0) > CACHE_BREAK_TOKENS:
                breaks += 1
        for sub in glob.glob(os.path.join(os.path.dirname(paths[0]), sid, "subagents", "*.jsonl")):
            sub_usages = _usages(sub)
            sub_requests += len(sub_usages)
            usages = usages + sub_usages
        for u in usages:
            for k in TOKEN_KEYS:
                tokens[k] += int(u.get(k) or 0)
    if not found:
        return None
    return {"requests": len(contexts), "peak_context": max(contexts, default=0),
            "median_context": int(statistics.median(contexts)) if contexts else 0,
            "cache_breaks": breaks, "subagent_requests": sub_requests, "tokens": tokens}


def _usages(path):
    by_id = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("usage"), dict):
                    by_id[msg.get("id") or len(by_id)] = msg["usage"]
    except OSError:
        pass
    return list(by_id.values())


def restored_state(sid):
    """What `claude --resume <sid>` starts its cost counters from: 2.1.272 restores totalCostUSD and
    modelUsage from the transcript's last `cost-state` entry, so a resumed -p call reports
    session-cumulative total_cost_usd/modelUsage. None when the transcript has no such entry."""
    last = None
    for path in glob.glob(os.path.join(projects_dir(), "*", sid + ".jsonl")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "cost-state" not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("type") == "cost-state" \
                            and rec.get("sessionId") in (None, sid):
                        last = rec
        except OSError:
            pass
    return last


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def invocation_delta(res, restored):
    """(cost, model_usage) billed by this invocation alone: the reported session totals minus the
    restored cost-state. If the report is below the restored total, nothing was restored: use it as is."""
    reported = res.get("total_cost_usd")
    mu = res.get("modelUsage") if isinstance(res.get("modelUsage"), dict) else {}
    base_cost = float(restored.get("totalCostUSD") or 0) if restored else 0.0
    if not restored or not _num(reported) or reported + 1e-9 < base_cost:
        return (float(reported) if _num(reported) else None), mu
    base_mu = restored.get("modelUsage") if isinstance(restored.get("modelUsage"), dict) else {}
    delta = {}
    for model, usage in mu.items():
        prev = base_mu.get(model) if isinstance(base_mu.get(model), dict) else {}
        delta[model] = {k: max(0, v - prev.get(k, 0)) if _num(prev.get(k, 0)) else v
                        for k, v in (usage or {}).items() if _num(v)}
    return max(0.0, float(reported) - base_cost), delta


def _tail(text, n=2000):
    text = text or ""
    return text if len(text) <= n else "…" + text[-n:]


def exclude_in_git(repo, rel):
    info = os.path.join(repo, ".git", "info")
    if os.path.isdir(os.path.join(repo, ".git")):
        os.makedirs(info, exist_ok=True)
        with open(os.path.join(info, "exclude"), "a", encoding="utf-8") as f:
            f.write("/" + rel.lstrip("/") + "\n")


def execute_run(run, arm, task, opts, work_root):
    """One run, never raising (except KeyboardInterrupt). Outcome classes:
    - infra_error: the bench, not the arm, failed (setup, claude not runnable, an unbilled is_error
      result such as an auth failure, a bench exception). Excluded from report statistics.
    - agent_error: a timeout, crash or unparseable output, which the arm may cause. Counts as a failed
      run; its cost is unknown (cost_usd None, cost_known_usd = the part that was reported)."""
    wdir = run_dir(work_root, run)
    rec = {k: run[k] for k in ("order", "arm", "task", "rep", "warmup")}
    rec.update(started=now(), workdir=wdir, infra_error=None, agent_error=None, invocations=[], cost_usd=None,
               **{"pass": False})
    try:
        _execute_run(rec, arm, task, opts, work_root, wdir)
    except Exception as e:  # one broken run must not kill the matrix
        rec.update(infra_error=f"bench error: {e!r}"[:300], **{"pass": False})
    rec.setdefault("finished", now())
    return rec


def _execute_run(rec, arm, task, opts, work_root, wdir):
    repo = os.path.join(wdir, "repo")
    os.makedirs(repo)
    env = clean_env(arm)
    try:
        setup = subprocess.run(["sh", task["setup"], repo], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        rec["infra_error"] = "setup timed out"
        return
    if setup.returncode != 0:
        rec.update(infra_error="setup failed", setup_output=_tail(setup.stdout + setup.stderr))
        return
    for dest, src in arm["project_files"].items():
        target = os.path.join(repo, dest)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, src), target)
        exclude_in_git(repo, dest)

    sessions, answer, totals = {}, "", {"cost_usd": 0.0, "num_turns": 0, "duration_ms": 0}
    usage = {k: 0 for k in TOKEN_KEYS}
    model_usage = {}
    for kind, idx, prompt, mode, key in plan_invocations(arm, task):
        sid = sessions.setdefault(key, str(uuid.uuid4()))
        flag = ["--session-id", sid] if mode == "new" else ["--resume", sid]
        argv = claude_argv(opts.claude, prompt, arm, task, settings_path_for(work_root, arm), opts, flag)
        inv = {"kind": kind, "turn": idx, "session_id": sid}
        rec["invocations"].append(inv)
        restored = restored_state(sid) if mode == "resume" else None
        try:
            # stdin closed: `claude -p` otherwise waits 3s for piped input on every call
            proc = subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=opts.timeout)
        except subprocess.TimeoutExpired:
            inv["agent_error"] = f"timeout after {opts.timeout}s"
            continue
        except OSError as e:
            inv["infra_error"] = f"cannot run claude: {e}"
            break
        inv["exit_code"] = proc.returncode
        try:
            res = json.loads(proc.stdout)
        except ValueError:
            res = None
        if not isinstance(res, dict):
            inv.update(agent_error=f"unparseable output (exit {proc.returncode})", stdout=_tail(proc.stdout),
                       stderr=_tail(proc.stderr))
            continue
        cost, mu = invocation_delta(res, restored)
        inv.update(subtype=res.get("subtype"), is_error=res.get("is_error"), cost_usd=cost,
                   reported_cost_usd=res.get("total_cost_usd"), num_turns=res.get("num_turns"),
                   duration_ms=res.get("duration_ms"), session_id=res.get("session_id") or sid)
        if restored:
            inv["restored_cost_usd"] = restored.get("totalCostUSD")
        totals["cost_usd"] += cost or 0.0
        totals["num_turns"] += int(res.get("num_turns") or 0)
        totals["duration_ms"] += int(res.get("duration_ms") or 0)
        for k in usage:
            usage[k] += int((res.get("usage") or {}).get(k) or 0)
        for model, m in mu.items():
            acc = model_usage.setdefault(model, {})
            for k, v in (m or {}).items():
                if _num(v):
                    acc[k] = acc.get(k, 0) + v
        if res.get("is_error") and not cost:
            # nothing was billed: auth/API/startup failure, not an agent outcome
            inv["infra_error"] = "api error: " + str(res.get("result") or res.get("errors") or "")[:300]
            break
        if kind == "turn":
            answer = res.get("result") or ""

    rec["session_ids"] = sorted({i["session_id"] for i in rec["invocations"]})
    infra = [i["infra_error"] for i in rec["invocations"] if i.get("infra_error")]
    agent = [i["agent_error"] for i in rec["invocations"] if i.get("agent_error")]
    known = [i for i in rec["invocations"] if i.get("cost_usd") is not None]
    complete = not infra and not agent and len(known) == len(rec["invocations"])
    rec.update({
        "cost_usd": round(totals["cost_usd"], 6) if complete else None,
        "cost_known_usd": round(totals["cost_usd"], 6),
        "billed": any(i.get("cost_usd") for i in known),
        "num_turns": totals["num_turns"],
        "duration_ms": totals["duration_ms"],
        "subtypes": [i.get("subtype") or i.get("infra_error") or i.get("agent_error") for i in rec["invocations"]],
        "is_error": bool(infra or agent or any(i.get("is_error") for i in rec["invocations"])),
        "usage": usage,
        "model_usage": model_usage,
        "answer_tail": _tail(answer, 1500),
    })
    if infra:
        rec["infra_error"] = infra[0]
    elif agent:
        rec["agent_error"] = agent[0]
    if not infra:
        answer_path = os.path.join(wdir, "answer.txt")
        with open(answer_path, "w", encoding="utf-8") as f:
            f.write(answer)
        try:
            chk = subprocess.run(["sh", task["check"], repo, answer_path], cwd=repo, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
            check_exit, check_out = chk.returncode, _tail(chk.stdout + chk.stderr, 1000)
        except subprocess.TimeoutExpired:
            check_exit, check_out = -1, "check timed out"
        # a run that timed out or crashed failed, whatever state it left the repo in
        rec.update({"pass": check_exit == 0 and not agent, "check_pass": check_exit == 0,
                    "check_exit": check_exit, "check_output": check_out})
    rec["finished"] = now()
    rec["transcript"] = transcript_stats(rec["session_ids"])
    if opts.keep == "none" or (opts.keep == "failed" and rec["pass"]):
        shutil.rmtree(wdir, ignore_errors=True)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def git_sha():
    try:
        out = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain"], capture_output=True, text=True,
                               timeout=10)
        return {"sha": out.stdout.strip() or None, "dirty": bool(dirty.stdout.strip())}
    except Exception:
        return {"sha": None, "dirty": None}


def claude_version(claude):
    try:
        out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip().split()[0] if out.stdout.strip() else None
    except Exception:
        return None


def save(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def execute(arms, tasks, schedule, opts, run_id, work_root):
    version = claude_version(opts.claude)
    if opts.expect_version and version != opts.expect_version:
        raise BenchError(f"claude --version is {version!r}, expected {opts.expect_version!r} "
                         f"(pin it, or pass --expect-version '' to accept any)")
    for arm in arms:
        for src in arm["project_files"].values():
            if src and not os.path.exists(os.path.join(REPO, src)):
                raise BenchError(f"arm {arm['name']}: project file source missing: {src}")
        for d in arm["plugin_dirs"]:
            if not os.path.isdir(os.path.join(REPO, d)):
                raise BenchError(f"arm {arm['name']}: plugin dir missing: {d}")
    if BENCH_MARKER not in os.path.abspath(work_root):
        raise BenchError(f"work root must contain {BENCH_MARKER!r} (the field ledger skips those transcripts): "
                         f"{work_root}")
    if os.path.isdir(work_root) and os.listdir(work_root):
        raise BenchError(f"work root not empty: {work_root}")
    os.makedirs(os.path.join(work_root, "settings"), exist_ok=True)
    for arm in arms:
        save(settings_path_for(work_root, arm), arm["settings"])
    os.makedirs(opts.out, exist_ok=True)
    path = os.path.join(opts.out, run_id + ".json")
    doc = {
        "schema": 1,
        "run_id": run_id,
        "started": now(),
        "finished": None,
        "meta": {"claude_version": version, "model": opts.model, "effort": opts.effort, "k": opts.k,
                 "warmup": opts.warmup, "seed": opts.seed, "budget_usd": opts.budget_usd,
                 "hetz": git_sha(), "platform": platform.platform(), "python": platform.python_version(),
                 "stripped_env": stripped_env_names(), "work_root": work_root, "bench_marker": BENCH_MARKER},
        "arms": {a["name"]: a for a in arms},
        "tasks": {t["name"]: {"turns": len(t["turns"]), "max_turns": t["max_turns"]} for t in tasks},
        "runs": [],
    }
    save(path, doc)
    by_arm = {a["name"]: a for a in arms}
    by_task = {t["name"]: t for t in tasks}
    streak = 0
    try:
        for run in schedule:
            tag = " warm-up" if run["warmup"] else ""
            print(f"[{run['order'] + 1}/{len(schedule)}] {run['arm']} x {run['task']} rep {run['rep']}{tag} ...",
                  end=" ", flush=True)
            rec = execute_run(run, by_arm[run["arm"]], by_task[run["task"]], opts, work_root)
            doc["runs"].append(rec)
            save(path, doc)
            cost = "n/a" if rec.get("cost_usd") is None else f"${rec['cost_usd']:.3f}"
            err = rec.get("infra_error") or rec.get("agent_error")
            print(("PASS" if rec["pass"] else "FAIL") + f" {cost}" + (f" [{err}]" if err else ""), flush=True)
            streak = streak + 1 if rec.get("infra_error") or not rec.get("billed") else 0
            if opts.max_errors and streak >= opts.max_errors:
                print(f"aborting: {streak} runs in a row billed nothing or hit an infra error "
                      f"(last: {err or 'no cost reported'}); fix auth/setup/flags and rerun", file=sys.stderr)
                break
    except KeyboardInterrupt:
        print("\ninterrupted; partial results kept", file=sys.stderr)
    doc["finished"] = now()
    save(path, doc)
    print(f"results: {path}\nreport:  python3 {os.path.join(BENCH, 'report.py')} {path}")
    return path


# ---------------------------------------------------------------- cli

def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Dry-run unless --execute. See bench/README.md.")
    ap.add_argument("--arms", help="comma list of arm names (default: all in bench/arms)")
    ap.add_argument("--tasks", help="comma list of task names (default: all in bench/tasks)")
    ap.add_argument("--preset", choices=sorted(PRESETS), help="named arm/task selection (cheap = A,D x 3 tasks)")
    ap.add_argument("-k", type=int, default=5, help="measured repetitions per arm x task cell (default 5)")
    ap.add_argument("--warmup", type=int, default=1, help="warm-up runs per cell, discarded by report (default 1)")
    ap.add_argument("--seed", type=int, help="shuffle seed (default: random, recorded in results)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"pinned main model (default {DEFAULT_MODEL})")
    ap.add_argument("--effort", default=DEFAULT_EFFORT, help=f"pinned effort (default {DEFAULT_EFFORT})")
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET,
                    help=f"--max-budget-usd per claude invocation (default {DEFAULT_BUDGET:g})")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per claude invocation (default 1800)")
    ap.add_argument("--out", default=RESULTS_DIR, help="results dir (default bench/results)")
    ap.add_argument("--work-root", help=f"where fixtures are built; must contain '{BENCH_MARKER}' "
                                        f"(default $TMPDIR/{BENCH_MARKER}/<run_id>)")
    ap.add_argument("--keep", choices=["all", "failed", "none"], default="failed", help="keep fixture dirs")
    ap.add_argument("--claude", default="claude", help="claude executable (default: claude on PATH)")
    ap.add_argument("--max-errors", type=int, default=3,
                    help="abort after this many consecutive runs that hit an infra error or billed nothing, "
                         "e.g. auth (default 3; 0 = never)")
    ap.add_argument("--expect-version", default=EXPECT_VERSION,
                    help=f"abort --execute unless claude --version matches (default {EXPECT_VERSION}; '' = any)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan and commands (default)")
    mode.add_argument("--execute", action="store_true", help="really run claude (spends tokens)")
    ap.add_argument("--list", action="store_true", help="list arms and tasks, then exit")
    opts = ap.parse_args(argv)
    if opts.k < 1 or opts.warmup < 0:
        ap.error("-k must be >= 1 and --warmup >= 0")
    if opts.seed is None:
        opts.seed = random.SystemRandom().randrange(1, 2**31)
    return opts


def select(names_csv, preset_names, available):
    if names_csv:
        names = [n.strip() for n in names_csv.split(",") if n.strip()]
    else:
        names = list(preset_names) if preset_names else list(available)
    return list(dict.fromkeys(names))  # dedupe, keep order


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        specs = load_arm_specs()
        all_tasks = list_tasks()
        if opts.list:
            for name in sorted(specs):
                print(f"arm  {name:<16} {specs[name].get('description', '')}")
            for name in all_tasks:
                t = load_task(name)
                print(f"task {name:<16} {len(t['turns'])} turn(s), max_turns={t['max_turns']}")
            return 0
        preset = PRESETS.get(opts.preset, {})
        arms = [resolve_arm(n, specs) for n in select(opts.arms, preset.get("arms"), sorted(specs))]
        tasks = [load_task(n) for n in select(opts.tasks, preset.get("tasks"), all_tasks)]
        schedule = build_schedule(arms, tasks, opts.k, opts.warmup, opts.seed)
        if not schedule:
            raise BenchError("empty schedule (check --arms/--tasks and arms' only_tasks)")
        run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-s{opts.seed}"
        if os.path.exists(os.path.join(opts.out, run_id + ".json")):
            run_id += "-" + uuid.uuid4().hex[:6]
        work_root = opts.work_root or os.path.join(os.environ.get("TMPDIR") or "/tmp", BENCH_MARKER, run_id)
        if not opts.execute:
            try:
                print_plan(arms, tasks, schedule, opts, run_id, work_root)
                sys.stdout.flush()
            except BrokenPipeError:  # `run.py | head`
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            return 0
        execute(arms, tasks, schedule, opts, run_id, work_root)
        return 0
    except BenchError as e:
        print(f"run.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
