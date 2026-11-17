"""Fixtures shared by the data-layer tests."""

import json
import shutil

import pytest

from clickless.config import repo_path
from clickless.data.snapshot import write_manifest


@pytest.fixture
def stations_only(tmp_path):
    """A draft snapshot like snapshot-v1-draft-stations: fixture stations, no amenities."""
    snap = tmp_path / "snapshot"
    snap.mkdir()
    for name in ("stations.json", "vehicles.json", "places.json"):
        shutil.copy(repo_path("data/fixtures") / name, snap / name)
    (snap / "amenities.json").write_text(json.dumps({"_meta": {}, "records": []}), encoding="utf-8")
    (snap / "routes").mkdir()
    write_manifest(str(snap), extra={"built_at": "2026-11-17T17:40:41+00:00", "frozen": False,
                                     "sources": {"afdc": {"raw_file": "afdc_US_x.json"},
                                                 "osm": {"scope": "none", "stations_queried": 0}}})
    return snap
