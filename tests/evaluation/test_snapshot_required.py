"""#119: a benchmark split on the synthetic fixture world is refused before any item runs.

Every dev/test item names real places and curated vehicles that exist only in the snapshot; on the
fixture world they all came back rejected_incomplete and the run still wrote a report full of zeros.
Explicit --items runs (the fixture smoke file) and split runs on the snapshot keep working.
"""

import json
import re

import pytest

from clickless.config import load_config
from evaluation import harness, report
from evaluation.config_names import (
    SNAPSHOT_HINT,
    SnapshotRequired,
    config_path,
    load_named_config,
    require_snapshot,
)

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"
HINT = "Evaluation needs the snapshot: set CLICKLESS_DATA_SOURCE=snapshot (see README)"


def test_the_message_names_the_fix():
    assert SNAPSHOT_HINT == HINT


# --------------------------------------------------------------------------- refused


@pytest.mark.parametrize("env", [None, "fixtures"])  # config default (data.source) or explicit override
@pytest.mark.parametrize("split", ["dev", "test", "raw"])
def test_a_split_on_fixtures_is_refused(monkeypatch, env, split):
    if env is None:
        monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    else:
        monkeypatch.setenv("CLICKLESS_DATA_SOURCE", env)
    with pytest.raises(SnapshotRequired, match=r"Evaluation needs the snapshot: set CLICKLESS_DATA_SOURCE=snapshot"):
        harness.select_items(load_config("A"), split, None)


def test_the_message_says_where_fixtures_came_from(monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    with pytest.raises(SnapshotRequired, match=r"Config A runs on 'fixtures' \(data\.source\).*'dev' split"):
        require_snapshot("A", load_config("A"), "dev", None)
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "fixtures")
    with pytest.raises(SnapshotRequired, match=r"\(CLICKLESS_DATA_SOURCE\).*--items benchmark/requests/dev/fixture"):
        require_snapshot("A", load_config("A"), "dev", None)


def test_harness_run_refuses_before_building_the_pipeline(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)

    def no_pipeline(*_a, **_k):
        raise AssertionError("the pipeline must not be built for a refused run")

    monkeypatch.setattr(harness, "build_pipeline", no_pipeline)
    with pytest.raises(SnapshotRequired):
        harness.run("A", "dev", out_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_harness_cli_exits_with_the_message(monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    monkeypatch.setattr("sys.argv", ["python -m evaluation.harness", "--config", "A", "--split", "dev"])
    with pytest.raises(SystemExit, match="refused: " + re.escape(HINT)):
        harness.main()


@pytest.mark.parametrize("configs", [["A"], ["A", "B", "C"], ["C-noKG"]])
def test_run_all_refuses_before_running_or_writing_anything(monkeypatch, tmp_path, configs):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    calls = []
    with pytest.raises(SnapshotRequired):
        report.run_all(configs, split="dev", out_root=str(tmp_path), runner=lambda *a, **k: calls.append(a))
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_run_all_refuses_the_final_test_run_on_fixtures(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)  # FROZEN.json exists in the repo
    calls = []
    with pytest.raises(SnapshotRequired):
        report.run_all(["A"], split="test", final=True, out_root=str(tmp_path), runner=lambda *a, **k: calls.append(a))
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_make_eval_dev_exits_with_the_message(monkeypatch, tmp_path):
    """`make eval-dev` = python -m evaluation.report --configs A B C --split dev."""
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    with pytest.raises(SystemExit, match="refused: " + re.escape(HINT)):
        report.main(["--configs", "A", "B", "C", "--split", "dev", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- allowed


def test_explicit_fixture_items_still_run_on_fixtures(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "fixtures")
    items, excluded = harness.select_items(load_config("A"), "dev", [SMOKE])
    assert [i.id for i in items] == ["smoke-001", "smoke-002", "smoke-003"] and excluded == []
    require_snapshot("A", load_config("A"), "dev", [SMOKE])  # no error


def test_a_split_on_the_snapshot_is_allowed(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    items, _ = harness.select_items(load_config("A"), "dev", None)
    assert len(items) == 50


def test_run_all_with_fixture_items_runs_every_config(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    calls = []

    def runner(config, split, limit, out_dir, items_paths, n_boot):
        calls.append(config)
        raise RuntimeError("stub")  # recorded as a failed config; the guard did not fire

    d = report.run_all(["A", "B"], items_paths=[SMOKE], out_root=str(tmp_path), runner=runner, stamp="s")
    assert calls == ["A", "B"]
    assert set(json.loads((d / "report_inputs.json").read_text(encoding="utf-8"))["failed"]) == {"A", "B"}


def test_run_all_on_the_snapshot_passes_the_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    calls = []

    def runner(config, split, limit, out_dir, items_paths, n_boot):
        calls.append((config, split))
        raise RuntimeError("stub")

    report.run_all(["A", "C-noKG"], split="dev", out_root=str(tmp_path), runner=runner, stamp="s")
    assert calls == [("A", "dev"), ("C-noKG", "dev")]


def test_named_ablation_configs_resolve_in_the_report(monkeypatch):
    """The report resolves C-noKG like the harness (before #119, load_config('C-noKG') failed there)."""
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    assert config_path("C-noKG").name == "config_c_nokg.yaml" and harness._config_path("c").name == "config_c.yaml"
    assert load_named_config("C-noKG")["name"] == "C-noKG"
    assert report.snapshot_id("C-noKG") == "fixtures"
