"""Snapshot validator. LEARNING SWAP (Ian) inside Aarya's module -- reviewed by Aarya.

Checks every record of a DataStore before the freeze:
  * coordinates inside the US bounding boxes; no duplicate ids
  * every station has >= 1 port with a normalized connector; DC ports have power_kw or a flag
  * provenance completeness: source, retrieved_at always; source_updated_at reported as a share
  * vehicles: every curated numeric field has a field_provenance entry
Output feeds the "provenance completeness" metric (proposal §V-C).
"""

from __future__ import annotations

from .loaders import DataStore


def validate_store(store: DataStore) -> dict[str, list[str]]:
    """Return {check_name: [problem descriptions]}; empty lists mean the check passed."""
    raise NotImplementedError("TODO(Ian, learning swap): snapshot validation checks")
