"""MANIFEST.json keys are portable `/` paths on every OS (#100)."""

import json
from pathlib import PurePosixPath, PureWindowsPath

from clickless.data.snapshot import manifest_key, verify_manifest, write_manifest


def test_windows_paths_become_forward_slash_keys():
    root = PureWindowsPath(r"C:\Users\me\repo\data\snapshot")
    assert manifest_key(root / "routes" / "phoenix__flagstaff.json", root) == "routes/phoenix__flagstaff.json"
    assert manifest_key(root / "stations.json", root) == "stations.json"


def test_posix_paths_are_unchanged():
    root = PurePosixPath("/repo/data/snapshot")
    assert manifest_key(root / "routes" / "a__b.json", root) == "routes/a__b.json"


def test_files_and_record_counts_use_the_same_keys(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "a__b.json").write_text(json.dumps({"records": [1, 2]}), encoding="utf-8")
    (tmp_path / "stations.json").write_text(json.dumps({"records": [1]}), encoding="utf-8")
    manifest = json.loads(write_manifest(str(tmp_path)).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"routes/a__b.json", "stations.json"}
    assert manifest["record_counts"] == {"routes/a__b.json": 2, "stations.json": 1}
    assert all("\\" not in key for key in [*manifest["files"], *manifest["record_counts"]])
    assert verify_manifest(str(tmp_path)) == []
