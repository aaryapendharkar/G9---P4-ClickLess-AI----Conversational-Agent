"""Dev/test split, freeze and double-annotation assignment (proposal §II). Owner: Ian.

Rules implemented:
  * 150 items -> 50 dev / 100 test, fixed seed 573, deterministic regardless of input order
    (items are sorted by id before anything random happens).
  * Deepti (parser owner) contributes DEV items only -> every item she authored goes to dev.
  * Leakage guard: items are grouped by their normalized origin<->destination pair (case,
    whitespace and punctuation insensitive; A->B == B->A). A group never straddles the split,
    and any group containing a Deepti item goes to dev. Items missing an origin or destination
    form a group of their own.
  * The remaining dev slots are filled so the test set's category and route_type mix stays as
    close as possible to the overall mix (greedy fill + swap refinement, seeded tie-breaks).
  * freeze(): writes benchmark/requests/dev/dev.jsonl, benchmark/requests/test/test.jsonl and
    benchmark/FROZEN.json (SHA-256 per split file, seed, counts, git commit, timestamp).
    tests/test_architecture.py fails CI if a hashed file changes afterwards; verify_frozen()
    does the same check for any freeze directory.
  * assign_double_annotation(): 40 items (8 per author, stratified by category), each assigned
    a second annotator who is neither the author nor the item's cross-checker. It only returns
    the assignment list; item files are never modified.

CLI:
    python -m evaluation.splits dry-run [--raw DIR]        # print the split summary, write nothing
    python -m evaluation.splits freeze  [--raw DIR]        # write the split files + FROZEN.json
    python -m evaluation.splits assign-double [--raw DIR]  # print "item_id<TAB>second_annotator"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.schema import BenchmarkItem, load_items

SEED = 573
N_TOTAL = 150
N_DEV = 50
N_TEST = N_TOTAL - N_DEV
PARSER_OWNER = "deepti"
AUTHORS = ("deepti", "aarya", "manas", "hriday", "ian")
N_DOUBLE_PER_AUTHOR = 8

DEV_FILE = Path("requests/dev/dev.jsonl")
TEST_FILE = Path("requests/test/test.jsonl")
FROZEN_FILE = Path("FROZEN.json")


# --------------------------------------------------------------------------- helpers


def _coerce(items) -> list[BenchmarkItem]:
    """Validate (dicts or BenchmarkItems), reject duplicate ids, return sorted by id."""
    out = [i if isinstance(i, BenchmarkItem) else BenchmarkItem.model_validate(i) for i in items]
    dupes = sorted(k for k, n in Counter(i.id for i in out).items() if n > 1)
    if dupes:
        raise ValueError(f"duplicate item ids: {dupes}")
    return sorted(out, key=lambda i: i.id)


def normalize_place(place: Any) -> str:
    """Case-, whitespace- and punctuation-insensitive form of a place string."""
    if place is None:
        return ""
    return re.sub(r"[\W_]+", "", str(place).casefold())


def route_key(item: BenchmarkItem) -> str:
    """Undirected normalized origin<->destination key; items lacking either end are their own group."""
    o = normalize_place(item.gold_request.get("origin"))
    d = normalize_place(item.gold_request.get("destination"))
    if not o or not d:
        return f"__single__:{item.id}"
    a, b = sorted((o, d))
    return f"{a}<->{b}"


def _group(items: list[BenchmarkItem]) -> dict[str, list[BenchmarkItem]]:
    groups: dict[str, list[BenchmarkItem]] = defaultdict(list)
    for it in items:
        groups[route_key(it)].append(it)
    return dict(groups)


@dataclass
class SplitResult:
    dev: list[BenchmarkItem]
    test: list[BenchmarkItem]
    seed: int
    parser_owner_ids: list[str]  # Deepti's items (forced into dev)
    guard_ids: list[str]  # non-Deepti items pulled into dev because they share a route with a Deepti item
    shared_route_groups: dict[str, list[str]] = field(default_factory=dict)  # every multi-item route group

    @property
    def guard_dev_slots(self) -> int:
        return len(self.guard_ids)

    def __iter__(self):  # allows `dev, test = stratified_split(...)`
        return iter((self.dev, self.test))


def _mix_distance(test_items: list[BenchmarkItem], all_items: list[BenchmarkItem]) -> tuple[float, float]:
    """(marginal distance, joint distance) of the test mix from the overall mix, in item counts.

    Marginal = L1 over category shares + L1 over route_type shares (what we optimise);
    joint = L1 over (category, route_type) cells (tie-breaker only).
    """
    n_test, n_all = len(test_items), len(all_items)
    if n_test == 0:
        return (0.0, 0.0)

    def l1(key) -> float:
        overall = Counter(key(i) for i in all_items)
        test = Counter(key(i) for i in test_items)
        return sum(abs(test[k] - n_test * overall[k] / n_all) for k in set(overall) | set(test))

    marginal = l1(lambda i: i.category) + l1(lambda i: i.route_type)
    joint = l1(lambda i: (i.category, i.route_type))
    return (round(marginal, 9), round(joint, 9))


# --------------------------------------------------------------------------- split


def stratified_split(items, n_dev: int = N_DEV, seed: int = SEED) -> SplitResult:
    """Split items into dev (n_dev) and test (the rest).

    Returns a SplitResult (unpackable as `dev, test`). Raises ValueError if the parser-owner rule
    plus the leakage guard need more than n_dev dev slots, or if dev cannot be filled exactly
    without splitting a route group.
    """
    items = _coerce(items)
    if not 0 <= n_dev <= len(items):
        raise ValueError(f"n_dev={n_dev} but only {len(items)} items")
    groups = _group(items)

    forced_keys = sorted(k for k, g in groups.items() if any(i.author == PARSER_OWNER for i in g))
    forced = [i for k in forced_keys for i in groups[k]]
    if len(forced) > n_dev:
        raise ValueError(
            f"parser-owner rule + leakage guard force {len(forced)} items into dev but n_dev={n_dev}; "
            "rewrite some duplicated origin/destination pairs"
        )

    # Units the fill may move: whole route groups without a Deepti item, in seeded order.
    free_keys = sorted(k for k in groups if k not in set(forced_keys))
    random.Random(seed).shuffle(free_keys)

    dev_keys: list[str] = []
    test_keys = list(free_keys)
    remaining = n_dev - len(forced)

    def test_of(keys: list[str]) -> list[BenchmarkItem]:
        return [i for k in keys for i in groups[k]]

    # Greedy: repeatedly move the group to dev that best preserves the test mix.
    while remaining > 0:
        best = None
        for k in test_keys:
            size = len(groups[k])
            if size > remaining:
                continue
            cand = [x for x in test_keys if x != k]
            score = _mix_distance(test_of(cand), items)
            if best is None or score < best[0]:
                best = (score, k)
        if best is None:
            raise ValueError(f"cannot fill the last {remaining} dev slot(s) without splitting a route group")
        dev_keys.append(best[1])
        test_keys.remove(best[1])
        remaining -= len(groups[best[1]])

    # Refinement: swap equal-sized groups between dev-fill and test while the mix improves.
    current = _mix_distance(test_of(test_keys), items)
    improved = True
    while improved:
        improved = False
        for d in list(dev_keys):
            for t in list(test_keys):
                if len(groups[d]) != len(groups[t]):
                    continue
                cand_test = [t2 if t2 != t else d for t2 in test_keys]
                score = _mix_distance(test_of(cand_test), items)
                if score < current:
                    dev_keys[dev_keys.index(d)] = t
                    test_keys = cand_test
                    current, improved = score, True
                    break
            if improved:
                break

    dev = sorted(forced + test_of(dev_keys), key=lambda i: i.id)
    test = sorted(test_of(test_keys), key=lambda i: i.id)
    return SplitResult(
        dev=dev,
        test=test,
        seed=seed,
        parser_owner_ids=[i.id for i in forced if i.author == PARSER_OWNER],
        guard_ids=sorted(i.id for i in forced if i.author != PARSER_OWNER),
        shared_route_groups={k: [i.id for i in g] for k, g in sorted(groups.items()) if len(g) > 1},
    )


# --------------------------------------------------------------------------- freeze


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _jsonl_bytes(items: list[BenchmarkItem]) -> bytes:
    lines = [json.dumps(i.model_dump(mode="json"), ensure_ascii=False) for i in sorted(items, key=lambda i: i.id)]
    return ("\n".join(lines) + "\n").encode("utf-8")  # always LF, so the hash is platform-independent


def _counts(items: list[BenchmarkItem]) -> dict[str, Any]:
    return {
        "n": len(items),
        "author": dict(sorted(Counter(i.author for i in items).items())),
        "category": dict(sorted(Counter(i.category for i in items).items())),
        "route_type": dict(sorted(Counter(i.route_type for i in items).items())),
    }


def _check_freezable(dev: list[BenchmarkItem], test: list[BenchmarkItem]) -> None:
    problems = []
    if len(dev) + len(test) != N_TOTAL:
        problems.append(f"need exactly {N_TOTAL} valid items, got {len(dev) + len(test)}")
    if len(dev) != N_DEV or len(test) != N_TEST:
        problems.append(f"need {N_DEV} dev / {N_TEST} test, got {len(dev)} / {len(test)}")
    if overlap := sorted({i.id for i in dev} & {i.id for i in test}):
        problems.append(f"items in both splits: {overlap}")
    if unchecked := sorted(i.id for i in dev + test if i.cross_checked_by is None):
        problems.append(f"{len(unchecked)} item(s) with cross_checked_by null: {unchecked}")
    if self_checked := sorted(i.id for i in dev + test if i.cross_checked_by == i.author):
        problems.append(f"item(s) cross-checked by their own author: {self_checked}")
    if owner_in_test := sorted(i.id for i in test if i.author == PARSER_OWNER):
        problems.append(f"{PARSER_OWNER} items in test (parser-owner rule): {owner_in_test}")
    test_keys = {route_key(i) for i in test}
    if leaked := sorted(i.id for i in dev if route_key(i) in test_keys):
        problems.append(f"dev items sharing an origin/destination pair with test: {leaked}")
    if problems:
        raise ValueError("refusing to freeze:\n  - " + "\n  - ".join(problems))


def freeze(dev_items, test_items, out_dir: str | Path = "benchmark", seed: int = SEED) -> Path:
    """Write the dev and test split files plus FROZEN.json; return the FROZEN.json path.

    FROZEN.json "files" maps paths relative to out_dir's parent (the repo root for the default
    out_dir) to SHA-256 digests, the format tests/test_architecture.py checks in CI.
    Raises FileExistsError if a freeze (or either split file) already exists, ValueError if the
    items are not exactly 150 valid, cross-checked, leak-free items in a 50/100 split.
    """
    out = Path(out_dir)
    targets = [out / FROZEN_FILE, out / DEV_FILE, out / TEST_FILE]
    if existing := [str(p) for p in targets if p.exists()]:
        raise FileExistsError(f"refusing to overwrite an existing freeze: {existing}")

    dev, test = _coerce(dev_items), _coerce(test_items)
    _coerce(dev + test)  # duplicate ids across splits
    _check_freezable(dev, test)

    digests = {}
    for rel, split in ((DEV_FILE, dev), (TEST_FILE, test)):
        data = _jsonl_bytes(split)
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digests[(Path(out.resolve().name) / rel).as_posix()] = hashlib.sha256(data).hexdigest()

    meta = {
        "files": digests,
        "seed": seed,
        "counts": {"dev": _counts(dev), "test": _counts(test)},
        "git_commit": _git_commit(),
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    frozen = out / FROZEN_FILE
    frozen.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    return frozen


def verify_frozen(out_dir: str | Path = "benchmark") -> None:
    """Raise AssertionError if any file listed in FROZEN.json no longer matches its SHA-256."""
    out = Path(out_dir)
    meta = json.loads((out / FROZEN_FILE).read_text(encoding="utf-8"))
    root = out.resolve().parent
    for rel, digest in meta["files"].items():
        actual = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        if actual != digest:
            raise AssertionError(f"{rel} changed after freeze (expected {digest[:12]}..., got {actual[:12]}...)")


# --------------------------------------------------------------------------- double annotation


def _largest_remainder(counts: dict[str, int], n: int, rng: random.Random) -> dict[str, int]:
    total = sum(counts.values())
    quotas = {k: n * v / total for k, v in counts.items()}
    alloc = {k: int(q) for k, q in quotas.items()}
    keys = sorted(counts)
    rng.shuffle(keys)  # seeded tie-break among equal remainders
    keys.sort(key=lambda k: quotas[k] - alloc[k], reverse=True)  # stable sort keeps the shuffle for ties
    for k in keys[: n - sum(alloc.values())]:
        alloc[k] += 1
    return alloc


def assign_double_annotation(
    items, per_author: int = N_DOUBLE_PER_AUTHOR, seed: int = SEED, annotators: tuple[str, ...] = AUTHORS
) -> list[tuple[str, str]]:
    """Pick `per_author` items per author (stratified by category) and assign each a second annotator.

    The second annotator is never the item's author or its cross-checker, and the load is spread
    as evenly as possible across annotators. Returns [(item_id, second_annotator), ...] sorted by
    item id. Raises ValueError if an item is not cross-checked yet, an author has too few items,
    or no valid assignment exists.
    """
    items = _coerce(items)
    if unchecked := [i.id for i in items if i.cross_checked_by is None]:
        raise ValueError(f"cross-check every item before assigning double annotation; unchecked: {unchecked}")
    rng = random.Random(seed)

    chosen: list[BenchmarkItem] = []
    for author in annotators:
        own = [i for i in items if i.author == author]
        if len(own) < per_author:
            raise ValueError(f"{author} has {len(own)} items, need at least {per_author}")
        by_cat: dict[str, list[BenchmarkItem]] = defaultdict(list)
        for i in own:
            by_cat[i.category].append(i)
        alloc = _largest_remainder({c: len(v) for c, v in by_cat.items()}, per_author, rng)
        for cat in sorted(by_cat):
            pool = list(by_cat[cat])
            rng.shuffle(pool)
            chosen.extend(pool[: alloc[cat]])

    # Balanced assignment: bipartite matching of items to annotator slots (capacity = ceil(n / k)).
    cap = -(-len(chosen) // len(annotators))
    eligible = {}
    for it in chosen:
        cands = [a for a in annotators if a not in (it.author, it.cross_checked_by)]
        rng.shuffle(cands)
        eligible[it.id] = cands
    owner: dict[str, list[str]] = {a: [] for a in annotators}  # annotator -> item ids
    assigned: dict[str, str] = {}

    def place(item_id: str, seen: set[str]) -> bool:
        cands = sorted(eligible[item_id], key=lambda a: len(owner[a]))  # least-loaded first (stable)
        for a in cands:
            if a in seen:
                continue
            seen.add(a)
            if len(owner[a]) < cap:
                owner[a].append(item_id)
                assigned[item_id] = a
                return True
            for other in list(owner[a]):  # try to move one of a's items elsewhere
                owner[a].remove(other)
                del assigned[other]
                if place(other, seen):
                    owner[a].append(item_id)
                    assigned[item_id] = a
                    return True
                owner[a].append(other)
                assigned[other] = a
        return False

    for it in sorted(chosen, key=lambda i: i.id):
        if not place(it.id, set()):
            raise ValueError(f"no valid second annotator for {it.id} under a load cap of {cap}")
    return sorted(assigned.items())


# --------------------------------------------------------------------------- CLI


def summary_table(result: SplitResult) -> str:
    """Markdown tables: split x category, split x route_type, split x author."""
    lines = []
    for attr in ("category", "route_type", "author"):
        keys = sorted({getattr(i, attr) for i in result.dev + result.test})
        lines.append(f"| {attr} | dev | test | total |")
        lines.append("|---|---:|---:|---:|")
        for k in keys:
            d = sum(getattr(i, attr) == k for i in result.dev)
            t = sum(getattr(i, attr) == k for i in result.test)
            lines.append(f"| {k} | {d} | {t} | {d + t} |")
        lines.append(f"| **total** | {len(result.dev)} | {len(result.test)} | {len(result.dev) + len(result.test)} |")
        lines.append("")
    lines.append(
        f"Leakage guard: {result.guard_dev_slots} dev slot(s) used by non-{PARSER_OWNER} items sharing a route "
        f"with a {PARSER_OWNER} item ({', '.join(result.guard_ids) or 'none'}); "
        f"{len(result.parser_owner_ids)} {PARSER_OWNER} items; "
        f"{N_DEV - len(result.parser_owner_ids) - result.guard_dev_slots} slot(s) left for the stratified fill."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from clickless.config import repo_path

    ap = argparse.ArgumentParser(prog="python -m evaluation.splits")
    ap.add_argument("command", choices=["dry-run", "freeze", "assign-double"])
    ap.add_argument("--raw", default=None, help="directory of raw <author>.jsonl files")
    ap.add_argument("--out", default=None, help="benchmark directory (freeze only)")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    items = load_items(args.raw or repo_path("benchmark/requests/raw"))

    if args.command == "assign-double":
        for item_id, annotator in assign_double_annotation(items, seed=args.seed):
            print(f"{item_id}\t{annotator}")
        return
    result = stratified_split(items, seed=args.seed)
    print(summary_table(result))
    if args.command == "freeze":
        print(freeze(result.dev, result.test, out_dir=args.out or repo_path("benchmark"), seed=args.seed))


if __name__ == "__main__":
    main()
