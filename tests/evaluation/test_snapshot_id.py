"""evaluation.snapshot_id: the data a run used, recorded in every result's run_metadata."""

import hashlib
import json

from evaluation.harness import run_metadata
from evaluation.snapshot_id import snapshot_info

COMMIT = {"git_commit": "abc", "git_dirty": False}


def _snapshot_dir(tmp_path, frozen=False):
    d = tmp_path / "snap"
    d.mkdir()
    raw = json.dumps({"frozen": frozen, "created_at": "2026-11-19T04:43:00Z", "files": {}}).encode()
    (d / "MANIFEST.json").write_bytes(raw)
    return d, hashlib.sha256(raw).hexdigest()


def test_fixtures_source():
    assert snapshot_info({"data": {"source": "fixtures"}}, env={}) == {
        "snapshot_id": "fixtures", "snapshot_manifest_sha256": None}


def test_snapshot_id_is_the_manifest_hash(tmp_path):
    d, digest = _snapshot_dir(tmp_path)
    info = snapshot_info({"data": {"source": "snapshot", "snapshot_dir": str(d)}}, env={})
    assert info["snapshot_manifest_sha256"] == digest
    assert info["snapshot_id"] == f"snapshot:{digest[:12]} (draft, created 2026-11-19T04:43:00Z)"


def test_environment_overrides_the_config_source(tmp_path):
    d, digest = _snapshot_dir(tmp_path, frozen=True)
    cfg = {"data": {"source": "fixtures", "snapshot_dir": str(d)}}
    info = snapshot_info(cfg, env={"CLICKLESS_DATA_SOURCE": "snapshot"})
    assert info["snapshot_id"].startswith(f"snapshot:{digest[:12]} (frozen")


def test_missing_manifest(tmp_path):
    info = snapshot_info({"data": {"source": "snapshot", "snapshot_dir": str(tmp_path)}}, env={})
    assert info == {"snapshot_id": "snapshot (no MANIFEST.json)", "snapshot_manifest_sha256": None}


def test_run_metadata_records_the_snapshot(tmp_path):
    d, digest = _snapshot_dir(tmp_path)
    cfg = {"name": "A", "components": {"parser": "rules"}, "data": {"source": "snapshot", "snapshot_dir": str(d)}}
    meta = run_metadata(cfg, env={}, commit=COMMIT)
    assert meta["snapshot_manifest_sha256"] == digest and meta["snapshot_id"].startswith(f"snapshot:{digest[:12]}")


def test_harness_output_records_fixtures(tmp_path):
    from evaluation.harness import run

    out = run("A", items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"], out_dir=str(tmp_path), n_boot=20)
    meta = json.loads(out.read_text(encoding="utf-8"))["run_metadata"]
    assert meta["snapshot_id"] == "fixtures" and meta["snapshot_manifest_sha256"] is None
