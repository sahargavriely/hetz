"""report.py math and rendering on synthetic results (no claude, no network)."""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


report = _load("hetz_bench_report", os.path.join(BENCH, "report.py"))


def run(arm, task, cost, passed=True, warmup=False, infra=None, peak=None, turns=4):
    return {"arm": arm, "task": task, "cost_usd": cost, "pass": passed, "warmup": warmup,
            "infra_error": infra, "num_turns": turns,
            "transcript": None if peak is None else {"peak_context": peak}}


def cell(arm, task, costs, fails=0, peaks=None):
    peaks = peaks or [None] * len(costs)
    return [run(arm, task, c, passed=i >= fails, peak=p) for i, (c, p) in enumerate(zip(costs, peaks))]


A1 = [1.0, 1.2, 0.8, 1.1, 0.9]        # median 1.0, Q1 0.9, Q3 1.1
D1 = [0.7, 0.8, 0.6, 0.75, 0.65]      # median 0.7 -> -30%
A2 = [2.0, 2.1, 1.9, 2.05, 1.95]      # median 2.0
D2 = [1.9, 1.85, 1.95, 2.0, 1.8]      # median 1.9
NOSQ2 = [2.2, 2.3, 2.1, 2.25, 2.15]   # median 2.2: D is 13.6% cheaper with squeeze
NOCAP2 = list(D2)                      # no difference

ARMS = {
    "A": {"ablates": None},
    "D": {"ablates": None},
    "D-no-squeeze": {"ablates": "squeeze", "ablation_base": "D", "target_tasks": ["t2"]},
    "D-no-cap": {"ablates": "cap", "ablation_base": "D", "target_tasks": ["t2"]},
}


def synthetic():
    runs = []
    runs += cell("A", "t1", A1) + cell("A", "t2", A2)
    runs += cell("D", "t1", D1, fails=1) + cell("D", "t2", D2)
    runs += cell("D-no-squeeze", "t2", NOSQ2) + cell("D-no-cap", "t2", NOCAP2)
    runs.append(run("A", "t1", 100.0, warmup=True))      # warm-up: must be ignored
    runs.append(run("D", "t1", 50.0, infra="setup failed"))  # infra error: must be ignored
    return runs


# ---------------------------------------------------------------- primitives

def test_percentile_linear_interpolation():
    xs = [4, 1, 3, 2]
    assert report.percentile(xs, 0) == 1
    assert report.percentile(xs, 100) == 4
    assert report.percentile(xs, 25) == pytest.approx(1.75)
    assert report.percentile(xs, 50) == pytest.approx(2.5)
    assert report.percentile(xs, 75) == pytest.approx(3.25)
    assert report.percentile([7], 30) == 7
    assert report.percentile([], 50) is None


def test_median_and_iqr():
    assert report.median([3, 1, 2]) == 2
    assert report.median(A1) == pytest.approx(1.0)
    q1, q3 = report.iqr(A1)
    assert (q1, q3) == (pytest.approx(0.9), pytest.approx(1.1))
    assert report.iqr([]) is None


def test_delta_pct():
    assert report.delta_pct(1.0, 0.7) == pytest.approx(-30.0)
    assert report.delta_pct(2.0, 3.0) == pytest.approx(50.0)
    assert report.delta_pct(0, 1.0) is None
    assert report.delta_pct(None, 1.0) is None


def test_bootstrap_constant_data_is_exact():
    lo, hi = report.bootstrap_ci([([1.0] * 5, [0.5] * 5)], iters=200, seed=1)
    assert lo == pytest.approx(-50.0) and hi == pytest.approx(-50.0)


def test_bootstrap_is_seeded_and_brackets_estimate():
    pairs = [(A1, D1)]
    first = report.bootstrap_ci(pairs, iters=2000, seed="x")
    assert first == report.bootstrap_ci(pairs, iters=2000, seed="x")
    lo, hi = first
    assert lo <= -30.0 <= hi
    assert hi < 0  # D1 is clearly cheaper than A1


def test_bootstrap_overlapping_samples_include_zero():
    lo, hi = report.bootstrap_ci([([1.0, 1.2, 0.8, 1.1, 0.9], [0.9, 1.1, 1.0, 1.2, 0.8])], iters=2000, seed=3)
    assert lo < 0 < hi


def test_bootstrap_needs_two_values_per_side():
    assert report.bootstrap_ci([([1.0], [0.5, 0.6])]) is None
    assert report.bootstrap_ci([]) is None


def test_bootstrap_stratified_sum_of_medians():
    # two strata with constant values: delta of sums is exact
    lo, hi = report.bootstrap_ci([([1.0] * 3, [0.5] * 3), ([3.0] * 3, [3.0] * 3)], iters=100, seed=0)
    assert lo == pytest.approx(-12.5) and hi == pytest.approx(-12.5)  # (0.5+3)/(1+3) - 1


# ---------------------------------------------------------------- summarize

def test_summarize_cells_exclude_warmup_and_infra_errors():
    s = report.summarize(synthetic(), ARMS, iters=1000, seed=0)
    assert s["excluded"] == {"warmup": 1, "infra_error": 1, "cost_unknown": 0}
    cells = {(c["task"], c["arm"]): c for c in s["cells"]}
    a1, d1 = cells[("t1", "A")], cells[("t1", "D")]
    assert a1["n"] == 5 and a1["median_cost"] == pytest.approx(1.0)
    assert a1["iqr"] == (pytest.approx(0.9), pytest.approx(1.1))
    assert "delta_pct" not in a1  # baseline row
    assert d1["n"] == 5 and d1["passes"] == 4 and d1["pass_rate"] == pytest.approx(0.8)
    assert d1["delta_pct"] == pytest.approx(-30.0)
    assert d1["ci"][1] < 0
    assert d1["extra_fails"] == pytest.approx(1.0)
    assert d1["cost_per_pass"] == pytest.approx(sum(D1) / 4)
    assert s["cells"][0]["arm"] == "A"  # baseline listed first


def test_summarize_suite_gate():
    s = report.summarize(synthetic(), ARMS, iters=1000, seed=0)
    suite = {x["arm"]: x for x in s["suite"]}
    d = suite["D"]
    assert d["tasks"] == ["t1", "t2"]
    assert d["base_sum"] == pytest.approx(3.0) and d["arm_sum"] == pytest.approx(2.6)
    assert d["delta_pct"] == pytest.approx((2.6 - 3.0) / 3.0 * 100)
    assert d["non_inferior"] is True
    assert d["meets_suite_gate"] is True
    assert d["ci"][0] <= d["delta_pct"] <= d["ci"][1]
    # ablation arms only ran t2, so their suite row covers t2 only
    assert suite["D-no-squeeze"]["tasks"] == ["t2"]


def test_summarize_ablation_verdicts():
    s = report.summarize(synthetic(), ARMS, iters=1000, seed=0)
    abl = {x["component"]: x for x in s["ablations"]}
    sq = abl["squeeze"]
    assert sq["tasks"] == ["t2"]
    assert sq["delta_pct"] == pytest.approx((1.9 - 2.2) / 2.2 * 100)
    assert sq["ci"][1] < 0
    assert sq["verdict"] == "keep"
    cap = abl["cap"]
    assert cap["delta_pct"] == pytest.approx(0.0)
    assert cap["verdict"] == "not proven: opt-in"


def test_ablation_quality_drop_and_peak_context_rule():
    runs = cell("D", "t", [1.0] * 5, fails=3, peaks=[50000] * 5) + \
        cell("D-no-x", "t", [1.0] * 5, peaks=[100000] * 5)
    arms = {"D-no-x": {"ablates": "x", "target_tasks": ["t"]}}
    s = report.summarize(runs, arms, baseline="D", iters=200)
    (x,) = s["ablations"]
    assert x["peak_delta_pct"] == pytest.approx(-50.0)
    assert x["non_inferior"] is False
    assert x["verdict"] == "quality drop"
    runs = cell("D", "t", [1.0] * 5, peaks=[50000, 51000, 49000, 50500, 49500]) + \
        cell("D-no-x", "t", [1.0] * 5, peaks=[100000, 99000, 101000, 100500, 99500])
    (x,) = report.summarize(runs, arms, baseline="D", iters=200)["ablations"]
    assert x["verdict"] == "keep"  # same cost, half the peak context


def test_extra_fails_scales_by_run_count():
    base = {"n": 10, "fails": 2}
    arm = {"n": 5, "fails": 2}
    assert report.extra_fails(base, arm) == pytest.approx(1.0)


def test_render_markdown_has_all_sections():
    s = report.summarize(synthetic(), ARMS, iters=500, seed=0)
    text = report.render(s, [("f.json", {"model": "m", "effort": "medium", "claude_version": "2.1.272"})])
    assert "| t1 | D | 5 | 4/5 | $0.700 |" in text
    assert "-30.0%" in text
    assert "## Suite" in text and "PASS" in text
    assert "| squeeze | D-no-squeeze | D | 1 |" in text and "keep" in text
    assert "WARNING" not in text


def test_render_warns_on_mixed_pins():
    s = report.summarize(synthetic(), ARMS, iters=100)
    text = report.render(s, [("a", {"model": "m1"}), ("b", {"model": "m2"})])
    assert "WARNING: results mix different model" in text


# ---------------------------------------------------------------- cli

def test_cli_reads_results_file(tmp_path):
    doc = {"schema": 1, "meta": {"model": "m", "effort": "medium", "claude_version": "2.1.272"},
           "arms": ARMS, "runs": synthetic()}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(doc))
    script = os.path.join(BENCH, "report.py")
    out = subprocess.run([sys.executable, script, str(path), "--json", "--iters", "300"],
                         capture_output=True, text=True, check=True).stdout
    summary = json.loads(out)
    assert summary["runs_used"] == 30
    assert {c["arm"] for c in summary["cells"]} == {"A", "D", "D-no-squeeze", "D-no-cap"}
    md = subprocess.run([sys.executable, script, str(tmp_path), "--iters", "300"],
                        capture_output=True, text=True, check=True).stdout
    assert md.startswith("# hetz bench report")
    # deterministic: same seed, same output
    md2 = subprocess.run([sys.executable, script, str(tmp_path), "--iters", "300"],
                         capture_output=True, text=True, check=True).stdout
    assert md == md2


def test_cli_no_data_exits_2(tmp_path):
    proc = subprocess.run([sys.executable, os.path.join(BENCH, "report.py"), str(tmp_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 2


def test_tokens_prefer_model_usage_and_fall_back_to_usage():
    r = {"model_usage": {"opus": {"inputTokens": 10, "cacheReadInputTokens": 100, "cacheCreationInputTokens": 5,
                                  "outputTokens": 7, "costUSD": 0.1},
                         "haiku": {"inputTokens": 1, "cacheReadInputTokens": 50, "cacheCreationInputTokens": 0,
                                   "outputTokens": 3}},
         "usage": {"input_tokens": 999}}
    assert report.run_tokens(r) == {"input": 11, "cache_read": 150, "cache_write": 5, "output": 10}
    r = {"usage": {"input_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4,
                   "output_tokens": 5}}
    assert report.run_tokens(r) == {"input": 2, "cache_read": 3, "cache_write": 4, "output": 5}
    assert report.run_tokens({}) is None
    runs = [dict(run("A", "t", 1.0), model_usage={"m": {"inputTokens": n, "outputTokens": 1}},
                 transcript={"peak_context": 10, "median_context": n, "cache_breaks": n % 2}) for n in (1, 2, 3)]
    (c,) = report.summarize(runs, iters=10)["cells"]
    assert c["median_tokens"]["input"] == 2 and c["median_tokens"]["output"] == 1
    assert c["median_context"] == 2 and c["median_cache_breaks"] == 1
    assert "## Tokens per run" in report.render(report.summarize(runs, iters=10))


# ---------------------------------------------------------------- pooling, error classes, variants


def _write(path, runs, arms=None, meta=None):
    path.write_text(json.dumps({"schema": 1, "meta": meta or {"model": "m", "effort": "medium",
                                                              "claude_version": "2.1.272"},
                                "arms": arms if arms is not None else ARMS, "runs": runs}))
    return path


def _cli(*args):
    return subprocess.run([sys.executable, os.path.join(BENCH, "report.py"), *args, "--iters", "100"],
                          capture_output=True, text=True)


def test_dir_means_newest_file_unless_all(tmp_path):
    _write(tmp_path / "20260101-000000-s1.json", cell("A", "t", [9.0] * 3))
    _write(tmp_path / "20260201-000000-s2.json", cell("A", "t", [1.0] * 3))
    assert report.result_files([str(tmp_path)]) == [str(tmp_path / "20260201-000000-s2.json")]
    assert len(report.result_files([str(tmp_path)], pool_all=True)) == 2
    newest = json.loads(_cli(str(tmp_path), "--json").stdout)
    assert newest["runs_used"] == 3 and newest["cells"][0]["median_cost"] == 1.0
    pooled = json.loads(_cli(str(tmp_path), "--all", "--json").stdout)
    assert pooled["runs_used"] == 6 and len(pooled["sources"]) == 2


def test_missing_dir_and_corrupt_files_do_not_crash(tmp_path):
    proc = _cli(str(tmp_path / "nope"))
    assert proc.returncode == 2 and "no usable runs" in proc.stderr and "Traceback" not in proc.stderr
    (tmp_path / "bad.json").write_text("{not json")
    good = _write(tmp_path / "good.json", cell("A", "t", [1.0] * 3))
    proc = _cli(str(tmp_path / "bad.json"), str(good), "--json")
    assert proc.returncode == 0, proc.stderr
    assert "skipping" in proc.stderr and "bad.json" in proc.stderr
    assert json.loads(proc.stdout)["runs_used"] == 3


def test_pooling_refuses_conflicting_arm_definitions(tmp_path):
    a1 = _write(tmp_path / "1.json", cell("A", "t", [1.0] * 3), arms={"A": {"settings": {"outputStyle": "Concise"}}})
    a2 = _write(tmp_path / "2.json", cell("A", "t", [2.0] * 3), arms={"A": {"settings": {}}})
    proc = _cli(str(a1), str(a2))
    assert proc.returncode == 2 and "arm definitions differ" in proc.stderr and "A" in proc.stderr
    proc = _cli(str(a1), str(a2), "--allow-mixed")
    assert proc.returncode == 0 and "WARNING: arm definitions differ" in proc.stdout


def test_pooling_warns_on_different_or_dirty_hetz_code():
    s = report.summarize(synthetic(), ARMS, iters=50)
    m = {"model": "m", "effort": "e", "claude_version": "v"}
    text = report.render(s, [("a", dict(m, hetz={"sha": "aaaaaaaaaaaaaaaa", "dirty": False})),
                             ("b", dict(m, hetz={"sha": "bbbbbbbbbbbbbbbb", "dirty": False}))])
    assert "WARNING: results mix different hetz" in text and "aaaaaaaaaaaa/bbbbbbbbbbbb" in text
    text = report.render(s, [("a", dict(m, hetz={"sha": "aaaa", "dirty": True})),
                             ("b", dict(m, hetz={"sha": "aaaa", "dirty": True}))])
    assert "dirty hetz tree" in text
    text = report.render(s, [("a", dict(m, hetz={"sha": "aaaa", "dirty": True}))])
    assert "hetz aaaa+dirty" in text and "WARNING" not in text


def test_agent_errors_count_as_failures_with_unknown_cost():
    base = cell("A", "t", [1.0] * 5) + cell("A", "u", [1.0] * 5)
    # arm X: cheaper where it finishes, but times out on 2 of 5 runs of t and on every run of u
    x = cell("X", "t", [0.5] * 3)
    x += [dict(run("X", "t", None, passed=False), agent_error="timeout after 1800s") for _ in range(2)]
    x += [dict(run("X", "u", None, passed=False), agent_error="timeout after 1800s") for _ in range(5)]
    s = report.summarize(base + x, {}, iters=200)
    cells = {(c["task"], c["arm"]): c for c in s["cells"]}
    xt = cells[("t", "X")]
    assert xt["n"] == 5 and xt["fails"] == 2 and xt["cost_unknown"] == 2 and xt["median_cost"] == 0.5
    xu = cells[("u", "X")]
    assert xu["n"] == 5 and xu["fails"] == 5 and xu["median_cost"] is None
    assert s["excluded"]["cost_unknown"] == 7 and s["runs_used"] == 20
    (suite,) = s["suite"]
    assert suite["tasks"] == ["t"] and suite["quality_tasks"] == ["t", "u"]
    assert suite["non_inferior"] is False and suite["meets_suite_gate"] is False
    text = report.render(s)
    assert "| t | X | 5 | 3/5 | $0.500 |" in text and "2 no $" in text


def test_infra_errors_are_excluded_and_shown_per_cell():
    runs = cell("A", "t", [1.0] * 3) + [run("A", "t", None, passed=False, infra="api error: auth")]
    s = report.summarize(runs, iters=50)
    (c,) = s["cells"]
    assert c["n"] == 3 and c["infra_excluded"] == 1
    assert "| 1 infra |" in report.render(s)


def test_variants_compare_against_their_base():
    runs = cell("D", "t", [1.0, 1.1, 0.9, 1.05, 0.95]) + cell("D-x", "t", [0.5, 0.55, 0.45, 0.52, 0.48])
    runs += cell("D-y", "t", [1.0, 1.1, 0.9, 1.05, 0.95], fails=3)
    arms = {"D-x": {"variant_of": "D"}, "D-y": {"variant_of": "D"}, "D-z": {"variant_of": "D"}}
    s = report.summarize(runs, arms, baseline="D", iters=300)
    v = {x["arm"]: x for x in s["variants"]}
    assert v["D-x"]["delta_pct"] == pytest.approx(-50.0) and v["D-x"]["verdict"] == "cheaper"
    assert v["D-x"]["passes"] == 5 and v["D-x"]["base_n"] == 5
    assert v["D-y"]["verdict"] == "quality drop"
    assert "D-z" not in v  # never ran
    text = report.render(s)
    assert "## Variants" in text and "| D-x | D | 1 | -50.0%" in text


def test_token_cross_check():
    r = dict(run("A", "t", 1.0), model_usage={"m": {"inputTokens": 10, "cacheReadInputTokens": 80,
                                                    "cacheCreationInputTokens": 10, "outputTokens": 5}},
             transcript={"peak_context": 1, "tokens": {"input_tokens": 10, "cache_read_input_tokens": 80,
                                                       "cache_creation_input_tokens": 9, "output_tokens": 2}})
    assert report.token_check(r) == pytest.approx(0.99)
    assert report.token_check(run("A", "t", 1.0)) is None
    s = report.summarize([r, dict(r)], iters=10)
    assert s["token_check"]["n"] == 2 and s["token_check"]["median"] == pytest.approx(0.99)
    assert "Token cross-check" in report.render(s)
