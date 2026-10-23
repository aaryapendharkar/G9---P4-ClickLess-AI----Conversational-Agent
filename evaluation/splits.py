"""Dev/test split and freeze (proposal §II). Owner: Ian.

Rules to implement:
  * 150 items -> 50 dev / 100 test, stratified by (category, route_type), fixed seed
  * Deepti (parser owner) contributes DEV items only -> force her items into dev
  * freeze(): write benchmark/requests/test/test.jsonl + benchmark/FROZEN.json with the SHA-256
    of the test file and the date; CI fails if the test file changes after that (test below)
"""

from __future__ import annotations


def stratified_split(items, n_dev: int = 50, seed: int = 573):
    raise NotImplementedError("TODO(Ian): stratified split honoring the parser-owner rule")


def freeze(dev_items, test_items, out_dir: str = "benchmark") -> None:
    raise NotImplementedError("TODO(Ian): write split files + FROZEN.json hash")
