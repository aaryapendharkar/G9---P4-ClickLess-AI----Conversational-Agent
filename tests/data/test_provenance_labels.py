"""Data-status labels (ADR 0010)."""

from datetime import datetime, timezone

from clickless.contracts import DataStatus, Provenance
from clickless.data.provenance import is_stale, status_label


def prov(status, updated=None, retrieved="2026-11-03"):
    def dt(s):
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if s else None

    return Provenance(source="AFDC", retrieved_at=dt(retrieved), source_updated_at=dt(updated), status=status)


def test_fresh_cached_record():
    assert status_label(prov(DataStatus.CACHED, "2026-09-30")) == "AFDC, cached snapshot (source updated 2026-09-30)"


def test_old_cached_record_is_flagged():
    p = prov(DataStatus.CACHED, "2024-01-15")
    assert is_stale(p) is True
    assert status_label(p) == "AFDC, cached snapshot (source updated 2024-01-15); may be out of date"


def test_cached_without_update_date_says_so():
    assert status_label(prov(DataStatus.CACHED)) == "AFDC, cached snapshot; source update date unknown"


def test_static_reference_data_has_no_age_note():
    p = prov(DataStatus.STATIC)
    assert is_stale(p) is None and status_label(p) == "AFDC, reference data"


def test_label_does_not_depend_on_wall_clock():
    p = prov(DataStatus.CACHED, "2026-09-30")
    assert status_label(p, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == status_label(p)
