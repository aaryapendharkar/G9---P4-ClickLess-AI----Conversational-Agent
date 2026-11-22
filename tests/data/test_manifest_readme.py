"""The tracked data/snapshot/README.md is documentation, not snapshot data: never hashed, and an
old manifest's README.md entry doesn't make an unzipped release fail verification."""

import hashlib
import json

from clickless.data.snapshot import DOCS_FILE, verify_manifest, write_manifest


def _snapshot(tmp_path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "stations.json").write_text(json.dumps({"records": [1]}), encoding="utf-8")
    (tmp_path / "routes" / "a__b.json").write_text(json.dumps({"records": [1, 2]}), encoding="utf-8")
    (tmp_path / DOCS_FILE).write_text("# How to download the snapshot\n", encoding="utf-8")
    return tmp_path


def test_write_manifest_skips_the_root_readme(tmp_path):
    root = _snapshot(tmp_path)
    (root / "routes" / "README.md").write_text("only the root guide is excluded\n", encoding="utf-8")
    manifest = json.loads(write_manifest(str(root)).read_text(encoding="utf-8"))
    assert DOCS_FILE not in manifest["files"] and DOCS_FILE not in manifest["record_counts"]
    assert set(manifest["files"]) == {"stations.json", "routes/a__b.json", "routes/README.md"}
    # editing the guide afterwards changes nothing the manifest checks
    (root / DOCS_FILE).write_text("# Updated guide\n", encoding="utf-8")
    assert verify_manifest(str(root)) == []


def test_verify_ignores_a_readme_entry_from_an_older_manifest(tmp_path):
    root = _snapshot(tmp_path)
    manifest = json.loads(write_manifest(str(root)).read_text(encoding="utf-8"))
    old_readme = b"# the README of the build that made the release\n"
    manifest["files"][DOCS_FILE] = hashlib.sha256(old_readme).hexdigest()  # as releases built before this fix
    raw = json.dumps(manifest, indent=2)
    (root / "MANIFEST.json").write_text(raw, encoding="utf-8")

    assert verify_manifest(str(root)) == []  # the committed README differs from the release's: still intact
    (root / DOCS_FILE).unlink()
    assert verify_manifest(str(root)) == []  # missing README: still intact
    assert (root / "MANIFEST.json").read_text(encoding="utf-8") == raw  # verifying never rewrites it


def test_verify_still_catches_real_data_changes(tmp_path):
    root = _snapshot(tmp_path)
    write_manifest(str(root))
    (root / "stations.json").write_text(json.dumps({"records": [1, 2]}), encoding="utf-8")
    (root / "routes" / "a__b.json").unlink()
    assert sorted(verify_manifest(str(root))) == ["routes/a__b.json", "stations.json"]
