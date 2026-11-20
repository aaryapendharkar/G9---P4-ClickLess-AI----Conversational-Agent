"""evaluation.report on synthetic result JSONs (fixture world), with hand-computed expectations."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.report import (
    SplitRefused,
    build_report,
    check_split,
    headline,
    load_runs,
    paired,
    run_all,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FORBIDDEN = ("clickless.planning", "clickless.ranking", "clickless.retrieval", "clickless.pipeline")

# Six items. gold: origin, destination, vehicle, start SoC (incomplete items: no start SoC).
#   id  author  category    route   clarification_required
#   i1  ian     urban       urban   no
#   i2  ian     rural       rural   no
#   i3  aarya   incomplete  mixed   yes
#   i4  aarya   urban       urban   no
#   i5  manas   rural       rural   no
#   i6  manas   incomplete  rural   yes
ITEMS = [
    ("i1", "ian", "urban", "urban", False),
    ("i2", "ian", "rural", "rural", False),
    ("i3", "aarya", "incomplete", "mixed", True),
    ("i4", "aarya", "urban", "urban", False),
    ("i5", "manas", "rural", "rural", False),
    ("i6", "manas", "incomplete", "rural", True),
]


def gold(item_id, incomplete):
    g = {"origin": "Phoenix, AZ", "destination": "Flagstaff, AZ", "vehicle_id": "fixture-spark-lr-2025"}
    return g if incomplete else {**g, "start_soc_pct": 50}


def write_items(path: Path) -> Path:
    lines = []
    for item_id, author, category, route, incomplete in ITEMS:
        lines.append(
            json.dumps(
                {
                    "id": item_id,
                    "author": author,
                    "category": category,
                    "route_type": route,
                    "text": item_id,
                    "gold_request": gold(item_id, incomplete),
                    "clarification_required": incomplete,
                    "missing_safety_fields": ["start_soc_pct"] if incomplete else [],
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


INCOMPLETE = {i for i, _a, _c, _r, inc in ITEMS if inc}


def plan_result(item_id, status, soc):
    req = {
        "request_id": item_id,
        "raw_text": item_id,
        "origin": {"text": "Phoenix, AZ"},
        "destination": {"text": "Flagstaff, AZ"},
        "vehicle_id": "fixture-spark-lr-2025",
    }
    if soc is not None:
        req["start_soc_pct"] = soc
    return {"request_id": item_id, "config": "X", "status": status, "request": req}


def row(item_id, status, soc="gold", latency=100.0, result=True):
    """soc='gold' predicts the gold start SoC (50, or none for incomplete items)."""
    if soc == "gold":
        soc = None if item_id in INCOMPLETE else 50
    r = {"id": item_id, "status": status}
    if latency is not None:  # the harness writes no latency for not_implemented rows
        r["latency_ms"] = latency
    if result:
        r["result"] = plan_result(item_id, status, soc)
    return r


META = {"git_commit": "abc1234", "git_dirty": False, "prompt_version": "n/a", "model": "n/a", "model_digest": "n/a"}


def payload(config, items_file, rows, metrics=None, findings=None, meta=None):
    return {
        "config": config,
        "split": None,
        "items": [str(items_file)],
        "n": len(rows),
        "run_metadata": {**META, "config": config, **(meta or {})},
        "metrics": metrics or {},
        "findings": findings or [],
        "rows": rows,
    }


# Config A: i2 has the wrong start SoC; i3 rejected (asked); i5 not_implemented (no result);
#           i6 planned without asking (silent assumption).
# Config B: every field right; asks on i3 and i6.
# Config C: same per-item outcomes as B, plus planning/grounding blocks and findings.
def rows_a():
    return [
        row("i1", "ok", latency=100),
        row("i2", "ok", soc=60, latency=110),
        row("i3", "rejected_incomplete", latency=90),
        row("i4", "ok", latency=105),
        row("i5", "not_implemented", latency=None, result=False),
        row("i6", "ok", latency=95),
    ]


def rows_b(latency=200.0):
    return [row(i, "needs_clarification" if inc else "ok", latency=latency) for i, _a, _c, _r, inc in ITEMS]


C_METRICS = {
    "planning": {
        "feasibility": {
            "plan_feasibility_rate": 0.9,
            "hard_violation_rate": 0.05,
            "ci95": {"plan_feasibility_rate": [0.8, 0.97]},
        }
    },
    "grounding": {"claim_support_rate": 0.95},
}
C_FINDINGS = [
    {"item_id": "i1", "kind": "verifier_disagreement", "detail": "planner ok, oracle not ok"},
    {"item_id": "i4", "kind": "planner_plan_outside_oracle_front", "detail": "alternative 1"},
]


@pytest.fixture
def run_dir(tmp_path):
    items = write_items(tmp_path / "items.jsonl")
    d = tmp_path / "20261117T080000Z"
    d.mkdir()
    (d / "A_items.json").write_text(json.dumps(payload("A", items, rows_a())), encoding="utf-8")
    b_metrics = {"planning": {"not_implemented": "TODO(Ian): planning"}}
    (d / "B_items.json").write_text(
        json.dumps(payload("B", items, rows_b(), b_metrics, meta={"model": "qwen3:8b", "model_digest": "500a1f"})),
        encoding="utf-8",
    )
    (d / "C_items.json").write_text(
        json.dumps(
            payload("C", items, rows_b(latency=200.0), C_METRICS, C_FINDINGS, meta={"prompt_version": "extract_v2"})
        ),
        encoding="utf-8",
    )
    inputs = {
        "split": None,
        "items": [str(items)],
        "final": False,
        "configs": ["A", "B", "C"],
        "snapshot_id": {"A": "fixtures", "B": "fixtures", "C": "fixtures"},
        "failed": {},
        "outputs": {},
    }
    (d / "report_inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    return d


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- headline


def test_headline_hand_computed(run_dir):
    runs, _ = load_runs(run_dir)
    a = headline(runs["A"], n_boot=200)
    # A has results for i1, i2, i3, i4, i6 (i5 not_implemented).
    # Field counts: i1 4 TP, i2 3 TP + 1 FP + 1 FN (start SoC 60 vs 50), i3 3 TP, i4 4 TP, i6 3 TP
    #   -> TP 17, FP 1, FN 1 -> F1 = 2*17 / (2*17 + 1 + 1) = 34/36
    assert a["field_f1"][0] == pytest.approx(34 / 36, rel=1e-6)
    # exact match: all but i2 -> 4/5
    assert a["exact_match"][0] == pytest.approx(4 / 5, rel=1e-6)
    # clarification: gold positives i3, i6; asked only i3 -> TP 1, FP 0, FN 1 -> F1 = 2/3
    assert a["clarification_f1"][0] == pytest.approx(2 / 3, rel=1e-6)
    # every predicted place carries the gold's state, so strict == city level here
    assert a["field_f1_strict"][0] == a["field_f1"][0] and a["exact_match_strict"][0] == a["exact_match"][0]
    # silent assumption: of i3, i6 the system proceeded on i6 -> 1/2
    assert a["silent_assumption_rate"][0] == pytest.approx(1 / 2, rel=1e-6)
    # errors over all six items: i5 (not_implemented, no parsed request) is a parse error -> 1/6
    assert a["parse_error_rate"][0] == pytest.approx(1 / 6, rel=1e-6)
    lo, hi = a["parse_error_rate"][1]
    assert 0.0 <= lo <= 1 / 6 <= hi <= 1.0
    assert a["planning_error_rate"][0] == 0.0
    # no planning / grounding / freshness blocks in A
    for col in ("plan_feasibility_rate", "hard_violation_rate", "claim_support_rate", "freshness_mislabel_rate"):
        assert a[col] == (None, None)

    b = headline(runs["B"], n_boot=200)
    assert b["field_f1"][0] == 1.0 and b["exact_match"][0] == 1.0 and b["clarification_f1"][0] == 1.0
    assert b["silent_assumption_rate"][0] == 0.0
    assert b["parse_error_rate"][0] == 0.0 and b["planning_error_rate"][0] == 0.0
    assert b["plan_feasibility_rate"] == (None, None)  # {"not_implemented": ...} -> n/a

    c = headline(runs["C"], n_boot=200)
    assert c["plan_feasibility_rate"] == (0.9, (0.8, 0.97))  # CI from the block's ci95
    assert c["hard_violation_rate"] == (0.05, None)  # value but no CI in the block
    assert c["claim_support_rate"] == (0.95, None)
    assert c["freshness_mislabel_rate"] == (None, None)  # block missing


# --------------------------------------------------------------------------- paired tests


def test_paired_a_vs_b_hand_computed(run_dir):
    runs, _ = load_runs(run_dir)
    rows = {r["outcome"]: r for r in paired(runs["A"], runs["B"])}
    # exact match: A succeeds on i1 i3 i4 i6, B on all -> b = 0 (A only), c = 2 (B only: i2, i5)
    #   exact two-sided binomial, 2 discordant: p = 2 * C(2,0) / 2^2 = 0.5
    em = rows["exact_match"]
    assert (em["test"], em["n"], em["b"], em["c"]) == ("McNemar", 6, 0, 2)
    assert em["p_value"] == pytest.approx(0.5, rel=1e-6)
    # clarification decision: A wrong on i5 (no result) and i6 (did not ask) -> c = 2
    assert (rows["clarification_correct"]["b"], rows["clarification_correct"]["c"]) == (0, 2)
    # no error: A errs on i5 only -> c = 1, p = 2 * C(1,0) / 2 = 1.0
    ne = rows["no_error"]
    assert (ne["b"], ne["c"]) == (0, 1) and ne["p_value"] == pytest.approx(1.0)
    # per-item field F1: i5 has no A score -> 5 pairs; only i2 differs (A 6/8 = 0.75 vs B 1.0)
    f1 = rows["field_f1"]
    assert (f1["test"], f1["n"]) == ("Wilcoxon", 5)
    assert f1["rate_a"] == pytest.approx((4 * 1.0 + 0.75) / 5, rel=1e-6)
    assert f1["note"] == "1 non-zero differences"
    # latency: A rows 100/110/90/105/95 (i5 none) vs B 200 everywhere -> 5 pairs, all differ
    assert rows["latency_ms"]["n"] == 5 and rows["latency_ms"]["p_value"] is not None


def test_paired_b_vs_c_identical_outcomes(run_dir):
    runs, _ = load_runs(run_dir)
    rows = {r["outcome"]: r for r in paired(runs["B"], runs["C"])}
    assert (rows["exact_match"]["b"], rows["exact_match"]["c"]) == (0, 0)
    assert rows["exact_match"]["p_value"] == 1.0  # no discordant pairs
    assert rows["field_f1"]["statistic"] is None  # every difference is zero -> Wilcoxon undefined


# --------------------------------------------------------------------------- full report


def test_build_report_writes_every_section_and_csv(run_dir):
    report = build_report(run_dir, n_boot=200)
    text = report.read_text(encoding="utf-8")
    for section in (
        "## Headline",
        "## Paired comparisons",
        "### By author",
        "### By category",
        "### By route_type",
        "## Findings",
        "## Run metadata",
    ):
        assert section in text
    for name in ("headline.csv", "paired.csv", "breakdown.csv", "errors.csv", "findings.csv", "metadata.csv"):
        assert (run_dir / name).is_file()
    assert "## Errors by stage and type" in text
    assert [(r["config"], r["stage"], r["error_type"], r["n"]) for r in read_csv(run_dir / "errors.csv")] == [
        ("A", "parse", "NotImplementedError", "1")
    ]

    head = {r["config"]: r for r in read_csv(run_dir / "headline.csv")}
    assert float(head["A"]["exact_match"]) == pytest.approx(0.8)
    assert head["A"]["plan_feasibility_rate"] == ""  # n/a in CSV
    assert float(head["C"]["plan_feasibility_rate_ci_lo"]) == pytest.approx(0.8)
    # markdown: missing block -> n/a, value without CI -> [CI n/a], value with CI -> [lo, hi]
    a_line = next(line for line in text.splitlines() if line.startswith("| A |"))
    c_line = next(line for line in text.splitlines() if line.startswith("| C |"))
    assert a_line.rstrip(" |").endswith("n/a")
    assert "0.900 [0.800, 0.970]" in c_line and "0.050 [CI n/a]" in c_line and "0.950 [CI n/a]" in c_line

    pairs = read_csv(run_dir / "paired.csv")
    assert {r["pair"] for r in pairs} == {"A vs B", "B vs C"}
    assert len(pairs) == 2 * 5  # 3 McNemar + 2 Wilcoxon outcomes per pair

    br = read_csv(run_dir / "breakdown.csv")
    a_author = {r["group"]: r for r in br if r["config"] == "A" and r["dimension"] == "author"}
    assert {g: int(r["n_items"]) for g, r in a_author.items()} == {"aarya": 2, "ian": 2, "manas": 2}
    assert float(a_author["manas"]["parse_error_rate"]) == pytest.approx(0.5)  # i5 errored, i6 ok
    assert float(a_author["manas"]["planning_error_rate"]) == 0.0
    assert int(a_author["manas"]["n_with_result"]) == 1
    a_cat = {r["group"]: int(r["n_items"]) for r in br if r["config"] == "A" and r["dimension"] == "category"}
    assert a_cat == {"incomplete": 2, "rural": 2, "urban": 2}
    a_route = {r["group"]: int(r["n_items"]) for r in br if r["config"] == "A" and r["dimension"] == "route_type"}
    assert a_route == {"mixed": 1, "rural": 3, "urban": 2}

    findings = read_csv(run_dir / "findings.csv")
    assert [(f["config"], f["item_id"], f["kind"]) for f in findings] == [
        ("C", "i1", "verifier_disagreement"),
        ("C", "i4", "planner_plan_outside_oracle_front"),
    ]
    meta = {r["config"]: r for r in read_csv(run_dir / "metadata.csv")}
    assert meta["A"]["git_commit"] == "abc1234" and meta["A"]["snapshot_id"] == "fixtures"
    assert meta["B"]["model"] == "qwen3:8b" and meta["B"]["model_digest"] == "500a1f"
    assert meta["C"]["prompt_version"] == "extract_v2"


def test_missing_config_shows_failed_and_skips_its_pair(tmp_path, run_dir):
    (run_dir / "C_items.json").unlink()
    inputs = json.loads((run_dir / "report_inputs.json").read_text(encoding="utf-8"))
    inputs["failed"] = {"C": "RuntimeError: config C uses an LLM but VOYAGER_BASE_URL is not set"}
    (run_dir / "report_inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    text = build_report(run_dir, n_boot=100).read_text(encoding="utf-8")
    assert "- C: RuntimeError: config C uses an LLM" in text
    assert "B vs C: n/a (C missing)" in text
    c_line = next(line for line in text.splitlines() if line.startswith("| C |"))
    assert c_line.count("n/a") == 12  # every headline metric
    assert {r["pair"] for r in read_csv(run_dir / "paired.csv")} == {"A vs B"}


# --------------------------------------------------------------------------- split guard and run_all


def test_test_split_refused_without_final_and_freeze(tmp_path):
    check_split("dev", final=False)  # dev is always allowed
    with pytest.raises(SplitRefused, match="--final"):
        check_split("test", final=False, frozen_file=tmp_path / "FROZEN.json")
    with pytest.raises(SplitRefused, match="does not exist"):
        check_split("test", final=True, frozen_file=tmp_path / "FROZEN.json")
    (tmp_path / "FROZEN.json").write_text("{}", encoding="utf-8")
    check_split("test", final=True, frozen_file=tmp_path / "FROZEN.json")


def test_run_all_refuses_test_before_running_anything(tmp_path):
    calls = []
    with pytest.raises(SplitRefused):
        run_all(["A"], split="test", out_root=str(tmp_path), runner=lambda *a, **k: calls.append(a))
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_run_all_records_failures_and_keeps_going(tmp_path):
    items = write_items(tmp_path / "items.jsonl")

    def runner(config, split, limit, out_dir, items_paths, n_boot):
        if config == "B":
            raise RuntimeError("config B uses an LLM but VOYAGER_BASE_URL is not set")
        out = Path(out_dir) / f"{config}_items.json"
        out.write_text(json.dumps(payload(config, items, rows_b())), encoding="utf-8")
        return out

    d = run_all(
        ["A", "B", "C"],
        items_paths=[str(items)],
        out_root=str(tmp_path / "results"),
        runner=runner,
        stamp="20261117T090000Z",
    )
    assert d == tmp_path / "results" / "20261117T090000Z"
    inputs = json.loads((d / "report_inputs.json").read_text(encoding="utf-8"))
    assert set(inputs["outputs"]) == {"A", "C"}
    assert inputs["failed"] == {"B": "RuntimeError: config B uses an LLM but VOYAGER_BASE_URL is not set"}
    assert inputs["snapshot_id"]["A"] == "fixtures"
    text = build_report(d, n_boot=100).read_text(encoding="utf-8")
    assert "A vs B: n/a (B missing)" in text and "B vs C: n/a (B missing)" in text


def test_end_to_end_config_a_on_the_fixture_world(tmp_path, monkeypatch):
    """The real harness, Config A, the fixture smoke items -> results/<ts>/report.md."""
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    d = run_all(
        ["A"],
        items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"],
        out_root=str(tmp_path),
        n_boot=50,
        stamp="e2e",
    )
    report = build_report(d, n_boot=50)
    head = {r["config"]: r for r in read_csv(d / "headline.csv")}
    assert head["A"]["status"] == "ok" and int(head["A"]["n_items"]) == 3
    assert float(head["A"]["parse_error_rate"]) == 0.0 and float(head["A"]["planning_error_rate"]) == 0.0
    assert "## Headline" in report.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- error stages, strict columns


def staged_rows():
    """Six items: i1 ok without state suffixes; i2/i4 planning errors (route not cached, from the
    result notes); i3 incomplete, proceeded and hit a planning error (a silent assumption);
    i5 a harness-tagged parse exception (no result); i6 asked."""
    def res(item_id, status, notes=(), states=True):
        r = plan_result(item_id, status, None if item_id in INCOMPLETE else 50)
        if not states:
            r["request"]["origin"]["text"], r["request"]["destination"]["text"] = "Phoenix", "Flagstaff"
        r["notes"] = list(notes)
        return {"id": item_id, "status": status, "latency_ms": 10.0, "result": r}

    route = ["no cached route at data/snapshot/routes/phoenix-az__flagstaff-az.json"]
    return [
        res("i1", "ok", states=False),
        res("i2", "error", route),
        res("i3", "error", route),
        res("i4", "error", route),
        {"id": "i5", "status": "error", "error": "ValueError: bad JSON from model",
         "error_type": "ValueError", "error_stage": "parse"},
        res("i6", "needs_clarification"),
    ]


def test_error_rate_split_by_stage_and_type(tmp_path):
    items = write_items(tmp_path / "items.jsonl")
    d = tmp_path / "run"
    d.mkdir()
    (d / "A_items.json").write_text(json.dumps(payload("A", items, staged_rows())), encoding="utf-8")
    runs, _ = load_runs(d)
    h = headline(runs["A"], n_boot=100)
    assert h["parse_error_rate"][0] == pytest.approx(1 / 6)  # i5
    assert h["planning_error_rate"][0] == pytest.approx(3 / 6)  # i2, i3, i4
    # planning errors keep their parse in the understanding metrics: i1-i4 and i6 scored, i5 not.
    # i1 has no state suffix -> matches at city level only.
    assert h["exact_match"][0] == 1.0
    assert h["exact_match_strict"][0] == pytest.approx(4 / 5)
    assert h["field_f1"][0] == 1.0 and h["field_f1_strict"][0] < 1.0
    # silent assumption: incomplete i3 (planning error after proceeding) and i6 (asked) -> 1/2
    assert h["silent_assumption_rate"][0] == pytest.approx(1 / 2)

    text = build_report(d, n_boot=100).read_text(encoding="utf-8")
    groups = [(r["stage"], r["error_type"], r["n"], r["example_ids"]) for r in read_csv(d / "errors.csv")]
    assert groups == [("parse", "ValueError", "1", "i5"), ("planning", "RouteNotCached", "3", "i2 i3 i4")]
    assert "| A | planning | RouteNotCached | 3 | 0.500 | i2 i3 i4 |" in text
    header = next(line for line in text.splitlines() if line.startswith("| Config | n |"))
    for label in ("Exact match (strict)", "Field F1 (strict)", "Parse error rate", "Planning error rate"):
        assert label in header


def test_fixture_refusal_stops_run_all_and_the_cli(tmp_path, monkeypatch):
    from evaluation import report
    from evaluation.schema import FixtureItemsRefused

    calls = []

    def runner(config, *a, **k):
        calls.append(config)
        raise FixtureItemsRefused("refusing 3 fixture-world item(s)")

    with pytest.raises(FixtureItemsRefused):
        run_all(["A", "B"], items_paths=["x.jsonl"], out_root=str(tmp_path), runner=runner, stamp="s")
    assert calls == ["A"]  # stops instead of recording a failed config and trying the next

    monkeypatch.setattr(report, "run_all", lambda *a, **k: runner("A"))
    with pytest.raises(SystemExit, match="refused: refusing 3 fixture-world"):
        report.main(["--configs", "A", "--items", "x.jsonl"])


# --------------------------------------------------------------------------- independence


def test_report_imports_no_system_code():
    import ast

    tree = ast.parse((SRC / "evaluation/report.py").read_text(encoding="utf-8"))
    names = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in names if m.startswith(FORBIDDEN)]


def test_building_a_report_loads_no_system_module():
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import evaluation.report\n"
        f"print('\\n'.join(sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r}))))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
