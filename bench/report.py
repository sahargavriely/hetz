#!/usr/bin/env python3
"""Summarize hetz bench results: median cost, IQR, pass rate, and delta % vs the baseline arm with a
seeded percentile-bootstrap 95% CI; suite and component-ablation verdicts per docs/DESIGN.md.

Usage: report.py [RESULTS.json | DIR ...] [--all] [--baseline A] [--json]
A DIR stands for its newest results file (by run_id), or all of them with --all; default DIR is
bench/results. Pooling refuses arms whose definitions differ between files (--allow-mixed overrides).
Python 3 stdlib only. Deterministic: the bootstrap RNG is seeded per comparison.
"""
import argparse
import glob
import json
import os
import random
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BENCH, "results")
SUITE_CUT_PCT = -10.0       # D vs A must cut median cost by >= 10%
COMPONENT_CUT_PCT = -3.0    # each component: >= 3% cost cut on its target tasks ...
COMPONENT_PEAK_PCT = -20.0  # ... or >= 20% lower peak context
MAX_EXTRA_FAILS = 1         # non-inferior: at most 1 extra failure per k runs of a task


# ---------------------------------------------------------------- math

def percentile(xs, q):
    """Linear-interpolated percentile, q in [0, 100] (numpy's default 'linear'); None if empty."""
    s = sorted(xs)
    if not s:
        return None
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def median(xs):
    return percentile(xs, 50)


def iqr(xs):
    """(Q1, Q3) or None."""
    return None if not xs else (percentile(xs, 25), percentile(xs, 75))


def delta_pct(base, value):
    """Relative change of value vs base, in percent; None when undefined."""
    if base is None or value is None or base == 0:
        return None
    return (value - base) / base * 100.0


def bootstrap_ci(pairs, iters=10000, seed=0, level=95.0):
    """Percentile-bootstrap CI for delta_pct(sum of base medians, sum of arm medians).

    pairs: [(base_values, arm_values), ...], one per stratum (task); each side is resampled with
    replacement within its stratum. Returns (lo, hi), or None if any side has fewer than 2 values.
    """
    if not pairs or any(len(b) < 2 or len(a) < 2 for b, a in pairs):
        return None
    rng = random.Random(seed)
    deltas = []
    for _ in range(iters):
        base = sum(median([rng.choice(b) for _ in b]) for b, _ in pairs)
        arm = sum(median([rng.choice(a) for _ in a]) for _, a in pairs)
        d = delta_pct(base, arm)
        if d is not None:
            deltas.append(d)
    if not deltas:
        return None
    tail = (100.0 - level) / 2
    return percentile(deltas, tail), percentile(deltas, 100.0 - tail)


# ---------------------------------------------------------------- data

ARM_DEF_KEYS = ("settings", "plugin_dirs", "env", "project_files", "model", "effort", "rotate", "extra_args",
                "prompt_prefix", "extra_turns")


def result_files(paths, pool_all=False):
    """Expand paths: a file is itself; a dir is its newest *.json (run_ids sort by start time), or all
    of them with pool_all. A missing dir yields nothing."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            found = sorted(glob.glob(os.path.join(p, "*.json")))
            files += found if pool_all else found[-1:]
        else:
            files.append(p)
    return files


def load(files, warn=None):
    """Read results files -> (runs, arms_meta, metas, conflicts). Unreadable files are skipped with a
    warning; conflicts lists arms whose definition differs between files."""
    warn = warn or (lambda msg: print(f"report.py: {msg}", file=sys.stderr))
    runs, arms, metas, defs, conflicts = [], {}, [], {}, []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError("not a results object")
        except (OSError, ValueError) as e:
            warn(f"skipping {path}: {e}")
            continue
        metas.append((path, doc.get("meta") or {}))
        for name, arm in (doc.get("arms") or {}).items():
            d = json.dumps({k: (arm or {}).get(k) for k in ARM_DEF_KEYS}, sort_keys=True)
            if name in defs and defs[name][0] != d and name not in conflicts:
                conflicts.append(name)
            defs.setdefault(name, (d, path))
            arms[name] = arm
        for run in doc.get("runs") or []:
            runs.append(dict(run, _source=os.path.basename(path)))
    return runs, arms, metas, conflicts


def measured(run, include_warmup=False):
    """Counts toward pass rate and non-inferiority: not warm-up, not an infra error. Timeouts and
    crashes (agent_error) stay in, as failures."""
    return (include_warmup or not run.get("warmup")) and not run.get("infra_error")


def costed(run):
    return isinstance(run.get("cost_usd"), (int, float)) and not isinstance(run.get("cost_usd"), bool)


def peak(run):
    t = run.get("transcript") or {}
    return t.get("peak_context")


TOKEN_KEYS = {  # report name: (modelUsage key, usage key)
    "input": ("inputTokens", "input_tokens"),
    "cache_read": ("cacheReadInputTokens", "cache_read_input_tokens"),
    "cache_write": ("cacheCreationInputTokens", "cache_creation_input_tokens"),
    "output": ("outputTokens", "output_tokens"),
}


def input_side(tokens):
    return None if not tokens else tokens["input"] + tokens["cache_read"] + tokens["cache_write"]


def token_check(run):
    """Transcript (main + subagents) / result modelUsage, input-side tokens; None if either is missing.
    Output is left out: transcripts under-record it."""
    t = (run.get("transcript") or {}).get("tokens")
    mu = run.get("model_usage") or {}
    if not t or not mu:
        return None
    result = input_side({name: sum(int(m.get(k) or 0) for m in mu.values()) for name, (k, _) in TOKEN_KEYS.items()})
    tx = input_side({name: int(t.get(k) or 0) for name, (_, k) in TOKEN_KEYS.items()})
    return tx / result if result else None


def run_tokens(run):
    """Token counts by type: summed over modelUsage (all models, subagents included), else usage."""
    mu = run.get("model_usage") or {}
    if mu:
        return {name: sum(int(m.get(k) or 0) for m in mu.values()) for name, (k, _) in TOKEN_KEYS.items()}
    usage = run.get("usage") or {}
    if usage:
        return {name: int(usage.get(k) or 0) for name, (_, k) in TOKEN_KEYS.items()}
    return None


def _med(values):
    values = [v for v in values if v is not None]
    return median(values)


def cell_stats(runs, infra=0):
    """runs: the cell's measured runs. Costs come from the runs whose cost is known."""
    priced = [r for r in runs if costed(r)]
    costs = [float(r["cost_usd"]) for r in priced]
    passes = sum(1 for r in runs if r.get("pass"))
    priced_passes = sum(1 for r in priced if r.get("pass"))
    peaks = [peak(r) for r in runs if peak(r) is not None]
    turns = [r.get("num_turns") for r in runs if r.get("num_turns") is not None]
    return {
        "n": len(runs),
        "passes": passes,
        "fails": len(runs) - passes,
        "pass_rate": passes / len(runs) if runs else None,
        "infra_excluded": infra,
        "cost_unknown": len(runs) - len(priced),
        "costs": costs,
        "median_cost": median(costs),
        "iqr": iqr(costs),
        "cost_per_pass": sum(costs) / priced_passes if priced_passes else None,
        "median_turns": median(turns),
        "peaks": peaks,
        "median_peak_context": median(peaks),
        "median_context": _med((r.get("transcript") or {}).get("median_context") for r in runs),
        "median_cache_breaks": _med((r.get("transcript") or {}).get("cache_breaks") for r in runs),
        "median_tokens": {name: _med((run_tokens(r) or {}).get(name) for r in runs) for name in TOKEN_KEYS},
    }


def extra_fails(base, arm):
    """Failures of arm beyond base, scaled to arm's run count."""
    if not base["n"] or not arm["n"]:
        return None
    return arm["fails"] - base["fails"] * arm["n"] / base["n"]


def non_inferior(stats, tasks, base_arm, arm):
    """(ok, tasks judged): at most MAX_EXTRA_FAILS extra failures of arm vs base on every task where
    both ran, including tasks whose costs are all unknown (e.g. every run timed out)."""
    judged = [t for t in tasks if stats.get((t, base_arm), {}).get("n") and stats.get((t, arm), {}).get("n")]
    extras = [extra_fails(stats[(t, base_arm)], stats[(t, arm)]) for t in judged]
    return bool(judged) and all(e is not None and e <= MAX_EXTRA_FAILS for e in extras), judged


def _compare(stats, tasks, base_arm, arm, metric, iters, seed):
    """Suite delta over tasks where both arms have data: (delta, ci, tasks_used, base_sum, arm_sum)."""
    key = "costs" if metric == "cost" else "peaks"
    used = [t for t in tasks if (t, base_arm) in stats and (t, arm) in stats
            and stats[(t, base_arm)][key] and stats[(t, arm)][key]]
    if not used:
        return None, None, [], None, None
    base_sum = sum(median(stats[(t, base_arm)][key]) for t in used)
    arm_sum = sum(median(stats[(t, arm)][key]) for t in used)
    pairs = [(stats[(t, base_arm)][key], stats[(t, arm)][key]) for t in used]
    ci = bootstrap_ci(pairs, iters, f"{seed}|{metric}|{base_arm}|{arm}|{','.join(used)}")
    return delta_pct(base_sum, arm_sum), ci, used, base_sum, arm_sum


def summarize(runs, arms_meta=None, baseline="A", iters=10000, seed=0, include_warmup=False):
    arms_meta = arms_meta or {}
    in_scope = [r for r in runs if include_warmup or not r.get("warmup")]
    kept = [r for r in in_scope if measured(r, include_warmup)]
    excluded = {"warmup": sum(1 for r in runs if r.get("warmup") and not include_warmup),
                "infra_error": sum(1 for r in runs if r.get("infra_error")),
                "cost_unknown": sum(1 for r in kept if not costed(r))}
    tasks = sorted({r["task"] for r in in_scope})
    arm_names = sorted({r["arm"] for r in in_scope}, key=lambda a: (a != baseline, a))
    stats = {}
    for t in tasks:
        for a in arm_names:
            cell = [r for r in in_scope if r["task"] == t and r["arm"] == a]
            if cell:
                stats[(t, a)] = cell_stats([r for r in cell if measured(r, include_warmup)],
                                           infra=sum(1 for r in cell if r.get("infra_error")))

    cells = []
    for (t, a), s in sorted(stats.items(), key=lambda kv: (kv[0][0], (kv[0][1] != baseline, kv[0][1]))):
        row = dict(task=t, arm=a, **{k: v for k, v in s.items() if k not in ("costs", "peaks")})
        b = stats.get((t, baseline))
        if b and a != baseline:
            row["delta_pct"] = delta_pct(b["median_cost"], s["median_cost"])
            row["ci"] = bootstrap_ci([(b["costs"], s["costs"])], iters, f"{seed}|cell|{t}|{baseline}|{a}")
            row["extra_fails"] = extra_fails(b, s)
        cells.append(row)

    suite = []
    for a in arm_names:
        if a == baseline:
            continue
        d, ci, used, bsum, asum = _compare(stats, tasks, baseline, a, "cost", iters, seed)
        non_inf, judged = non_inferior(stats, tasks, baseline, a)
        if not judged:
            continue
        n = sum(stats[(t, a)]["n"] for t in judged)
        passes = sum(stats[(t, a)]["passes"] for t in judged)
        suite.append({"arm": a, "tasks": used, "quality_tasks": judged, "base_sum": bsum, "arm_sum": asum,
                      "delta_pct": d, "ci": ci, "pass_rate": passes / n if n else None, "non_inferior": non_inf,
                      "meets_suite_gate": bool(non_inf and d is not None and d <= SUITE_CUT_PCT)})

    ablations = []
    for a in arm_names:
        meta = arms_meta.get(a) or {}
        if not meta.get("ablates"):
            continue
        base = meta.get("ablation_base") or "D"
        targets = [t for t in (meta.get("target_tasks") or tasks) if t in tasks]
        # component value = (with component) vs (without): base arm measured against the ablated arm
        d, ci, used, _, _ = _compare(stats, targets, a, base, "cost", iters, seed)
        pd, pci, _, _, _ = _compare(stats, targets, a, base, "peak", iters, seed)
        non_inf, judged = non_inferior(stats, targets, a, base)
        if not judged:
            ablations.append({"component": meta["ablates"], "arm": a, "base": base, "tasks": [],
                              "verdict": "no data"})
            continue
        cost_ok = d is not None and d <= COMPONENT_CUT_PCT and ci is not None and ci[1] < 0
        peak_ok = pd is not None and pd <= COMPONENT_PEAK_PCT and pci is not None and pci[1] < 0
        verdict = "keep" if non_inf and (cost_ok or peak_ok) else ("quality drop" if not non_inf else
                                                                   "not proven: opt-in")
        ablations.append({"component": meta["ablates"], "arm": a, "base": base, "tasks": used,
                          "quality_tasks": judged, "delta_pct": d, "ci": ci, "peak_delta_pct": pd,
                          "peak_ci": pci, "non_inferior": non_inf, "verdict": verdict})

    variants = []
    for a in arm_names:
        base = (arms_meta.get(a) or {}).get("variant_of")
        if not base or base == a:
            continue
        d, ci, used, _, _ = _compare(stats, tasks, base, a, "cost", iters, seed)
        pd, pci, _, _, _ = _compare(stats, tasks, base, a, "peak", iters, seed)
        non_inf, judged = non_inferior(stats, tasks, base, a)
        if not judged:
            variants.append({"arm": a, "base": base, "tasks": [], "verdict": "no data"})
            continue
        n = {x: sum(stats[(t, x)]["n"] for t in judged) for x in (a, base)}
        passes = {x: sum(stats[(t, x)]["passes"] for t in judged) for x in (a, base)}
        if not non_inf:
            verdict = "quality drop"
        elif ci and ci[1] < 0:
            verdict = "cheaper"
        elif ci and ci[0] > 0:
            verdict = "costlier"
        else:
            verdict = "no clear difference"
        variants.append({"arm": a, "base": base, "tasks": used, "quality_tasks": judged, "delta_pct": d, "ci": ci,
                         "peak_delta_pct": pd, "peak_ci": pci, "passes": passes[a], "n": n[a],
                         "base_passes": passes[base], "base_n": n[base], "non_inferior": non_inf,
                         "verdict": verdict})

    ratios = [x for x in (token_check(r) for r in kept) if x is not None]
    tcheck = {"n": len(ratios), "median": median(ratios), "min": min(ratios, default=None),
              "max": max(ratios, default=None)}

    return {"baseline": baseline, "runs_used": len(kept), "excluded": excluded, "tasks": tasks,
            "arms": arm_names, "cells": cells, "suite": suite, "ablations": ablations, "variants": variants,
            "token_check": tcheck, "bootstrap": {"iters": iters, "seed": seed}}


# ---------------------------------------------------------------- render

def money(x):
    return "-" if x is None else f"${x:.3f}"


def pct(x):
    return "-" if x is None else f"{x:+.1f}%"


def ci_s(ci):
    return "" if not ci else f" [{ci[0]:+.1f}, {ci[1]:+.1f}]"


def kilo(x):
    return "-" if x is None else f"{x / 1000:.1f}k"


def hetz_pin(meta):
    h = meta.get("hetz") or {}
    if not isinstance(h, dict) or not h.get("sha"):
        return "None"
    return str(h["sha"])[:12] + ("+dirty" if h.get("dirty") else "")


def pins_and_warnings(metas):
    """(pins {name: sorted values}, warnings) over the results files that were read."""
    pins = {}
    for _, m in metas:
        for k in ("claude_version", "model", "effort"):
            pins.setdefault(k, set()).add(str(m.get(k)))
        pins.setdefault("hetz", set()).add(hetz_pin(m))
    warnings = []
    mixed = [k for k, v in pins.items() if len(v) > 1]
    if mixed:
        warnings.append(f"results mix different {', '.join(mixed)}; deltas are not comparable.")
    if len(metas) > 1 and any(v.endswith("+dirty") for v in pins.get("hetz", ())):
        warnings.append("pooled results include a dirty hetz tree: the code may differ even at the same sha.")
    return {k: sorted(v) for k, v in pins.items()}, warnings


def excl_s(c):
    parts = []
    if c.get("infra_excluded"):
        parts.append(f"{c['infra_excluded']} infra")
    if c.get("cost_unknown"):
        parts.append(f"{c['cost_unknown']} no $")
    return ", ".join(parts) or "-"


def render(summary, metas=()):
    b = summary["baseline"]
    out = ["# hetz bench report", ""]
    ex = summary["excluded"]
    out.append(f"{summary['runs_used']} runs used ({ex['warmup']} warm-up and {ex['infra_error']} infra-error runs "
               f"excluded; {ex.get('cost_unknown', 0)} timed-out/crashed runs count as failures with unknown cost); "
               f"bootstrap {summary['bootstrap']['iters']} iters, seed {summary['bootstrap']['seed']}.")
    pins, warnings = pins_and_warnings(metas)
    if metas:
        out.append(f"Sources: {', '.join(os.path.basename(p) for p, _ in metas)}.")
    if pins:
        out.append("Pinned: " + ", ".join(f"{k} {'/'.join(v)}" for k, v in pins.items()) + ".")
    for w in warnings + list(summary.get("warnings") or []):
        out.append(f"WARNING: {w}")
    out.append("Costs are list-price estimates (total_cost_usd); compare relative deltas only.")
    out += ["", f"## Per task (delta vs {b})", "",
            f"| task | arm | n | pass | median $ | IQR $ | delta vs {b} [95% CI] | $/pass | turns | peak ctx | excluded |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in summary["cells"]:
        q = c["iqr"]
        iqr_s = "-" if not q else f"{money(q[0])}–{money(q[1])}"
        delta = "" if c["arm"] == b else pct(c.get("delta_pct")) + ci_s(c.get("ci"))
        turns = "-" if c["median_turns"] is None else format(c["median_turns"], "g")
        out.append(f"| {c['task']} | {c['arm']} | {c['n']} | {c['passes']}/{c['n']} | {money(c['median_cost'])} | "
                   f"{iqr_s} | {delta} | {money(c['cost_per_pass'])} | {turns} | "
                   f"{kilo(c['median_peak_context'])} | {excl_s(c)} |")
    out += ["", "## Tokens per run (medians; all models incl. subagents)", "",
            "| task | arm | input | cache read | cache write | output | median ctx | cache breaks |",
            "|---|---|---|---|---|---|---|---|"]
    for c in summary["cells"]:
        t = c["median_tokens"]
        breaks = "-" if c["median_cache_breaks"] is None else format(c["median_cache_breaks"], "g")
        out.append(f"| {c['task']} | {c['arm']} | {kilo(t['input'])} | {kilo(t['cache_read'])} | "
                   f"{kilo(t['cache_write'])} | {kilo(t['output'])} | {kilo(c['median_context'])} | {breaks} |")
    tc = summary.get("token_check") or {}
    if tc.get("n"):
        out += ["", f"Token cross-check, transcripts (main + subagents) / result modelUsage, input side: median "
                    f"{tc['median']:.3f}, range {tc['min']:.3f}–{tc['max']:.3f} over {tc['n']} runs "
                    f"(1.000 = agree; output is under-recorded in transcripts, so it is not compared)."]
    if summary["suite"]:
        out += ["", f"## Suite (sum of per-task medians over tasks shared with {b})", "",
                f"| arm | tasks | sum median $ | delta vs {b} [95% CI] | pass rate | non-inferior | "
                f"gate (<= {SUITE_CUT_PCT:g}%) |", "|---|---|---|---|---|---|---|"]
        for s in summary["suite"]:
            rate = "-" if s["pass_rate"] is None else f"{s['pass_rate']:.0%}"
            out.append(f"| {s['arm']} | {len(s['tasks'])} | {money(s['arm_sum'])} vs {money(s['base_sum'])} | "
                       f"{pct(s['delta_pct'])}{ci_s(s['ci'])} | {rate} | "
                       f"{'yes' if s['non_inferior'] else 'NO'} | {'PASS' if s['meets_suite_gate'] else 'fail'} |")
    if summary["ablations"]:
        out += ["", "## Components (base arm vs base-minus-component, on the component's target tasks)", "",
                "| component | ablation arm | base | tasks | cost delta [95% CI] | peak ctx delta [95% CI] | "
                "non-inferior | verdict |", "|---|---|---|---|---|---|---|---|"]
        for a in summary["ablations"]:
            if "non_inferior" not in a:
                out.append(f"| {a['component']} | {a['arm']} | {a['base']} | 0 | - | - | - | {a['verdict']} |")
                continue
            out.append(f"| {a['component']} | {a['arm']} | {a['base']} | {len(a['tasks'])} | "
                       f"{pct(a['delta_pct'])}{ci_s(a['ci'])} | {pct(a['peak_delta_pct'])}{ci_s(a['peak_ci'])} | "
                       f"{'yes' if a['non_inferior'] else 'NO'} | {a['verdict']} |")
        out.append("")
        out.append(f"keep = non-inferior and (cost <= {COMPONENT_CUT_PCT:g}% or peak ctx <= "
                   f"{COMPONENT_PEAK_PCT:g}%) with the 95% CI excluding 0.")
    if summary.get("variants"):
        out += ["", "## Variants (arm vs the arm it varies, on the tasks both ran)", "",
                "| arm | vs | tasks | cost delta [95% CI] | peak ctx delta [95% CI] | pass vs base | "
                "non-inferior | verdict |", "|---|---|---|---|---|---|---|---|"]
        for v in summary["variants"]:
            if "non_inferior" not in v:
                out.append(f"| {v['arm']} | {v['base']} | 0 | - | - | - | - | {v['verdict']} |")
                continue
            out.append(f"| {v['arm']} | {v['base']} | {len(v['quality_tasks'])} | {pct(v['delta_pct'])}{ci_s(v['ci'])} | "
                       f"{pct(v['peak_delta_pct'])}{ci_s(v['peak_ci'])} | {v['passes']}/{v['n']} vs "
                       f"{v['base_passes']}/{v['base_n']} | {'yes' if v['non_inferior'] else 'NO'} | {v['verdict']} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="results files or dirs (default: bench/results); "
                                             "a dir means its newest file unless --all")
    ap.add_argument("--all", action="store_true", help="pool every results file in the given dirs")
    ap.add_argument("--allow-mixed", action="store_true",
                    help="pool files even when an arm's definition differs between them (warns)")
    ap.add_argument("--baseline", default="A", help="control arm (default A)")
    ap.add_argument("--iters", type=int, default=10000, help="bootstrap resamples (default 10000)")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap seed (default 0)")
    ap.add_argument("--include-warmup", action="store_true", help="also count warm-up runs")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    opts = ap.parse_args(argv)
    paths = opts.paths or ([RESULTS_DIR] if os.path.isdir(RESULTS_DIR) else [])  # fresh clone: no results yet
    runs, arms, metas, conflicts = load(result_files(paths, opts.all))
    if conflicts and not opts.allow_mixed:
        print(f"report.py: arm definitions differ between results files: {', '.join(conflicts)}; "
              "report one file, or pass --allow-mixed", file=sys.stderr)
        return 2
    summary = summarize(runs, arms, opts.baseline, opts.iters, opts.seed, opts.include_warmup)
    if not summary["runs_used"]:
        print("report.py: no usable runs found", file=sys.stderr)
        return 2
    pins, warnings = pins_and_warnings(metas)
    summary["warnings"] = [f"arm definitions differ between files for {', '.join(conflicts)}; "
                           "their runs are pooled anyway (--allow-mixed)."] if conflicts else []
    if opts.json:
        summary.update(sources=[p for p, _ in metas], pins=pins, warnings=warnings + summary["warnings"])
        sys.stdout.write(json.dumps(summary, indent=1) + "\n")
    else:
        sys.stdout.write(render(summary, metas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
