# data/snapshot/ (not in git, except this README and MANIFEST.json)

## Use a snapshot someone already built
1. Download the snapshot zip from the GitHub release (or team drive) and unzip it **into this
   folder**, so `data/snapshot/MANIFEST.json` and `data/snapshot/stations.json` exist.
2. Run `make snapshot-verify`. It must print `verify_manifest() -> [] (intact)`. Anything else
   lists the files to download again; the app also refuses to load a mismatching snapshot.

## Build it (Aarya; needs internet)
1. Get a free API key at https://developer.nrel.gov/signup/ and put `NREL_API_KEY=...` in `.env`
   at the repo root (`.env` is git-ignored; never commit the key).
2. Trial run on a small area first: `make snapshot-build ARGS="--states AZ"`.
3. Full US: `make snapshot-build`. AFDC is one request; OSM is fetched in batches of 100 stations
   (default: available public DC-fast stations) with pauses, so the full run takes a while.
   Every download is cached under `data/raw/` and `data/cache/`: if it stops, run it again and
   it resumes. `--refresh-afdc` forces a new AFDC download.
4. The build loads the result back through `DataStore`, runs the validator and only writes
   `MANIFEST.json` when nothing blocking is found. Commit only `MANIFEST.json`; share the
   `.json` files and `routes/` through the team drive.
5. A normal build is a **draft** (`"frozen": false` in the manifest). The final build for the
   held-out evaluation uses `make snapshot-build ARGS="--freeze"`, which is refused until ADR
   0009, 0010 and 0011 all say `Status: accepted` (issue #32).

## Use it in a run (how configurations select the snapshot)
Each config has `data.source: fixtures | snapshot` (`configs/config_{a,b,c}.yaml`; all three
default to `fixtures`). Two ways to switch:

- for one run, without editing any file:
  `CLICKLESS_DATA_SOURCE=snapshot python -m clickless plan --config A "..."` (also works for
  `make eval-dev` and the harness);
- for good, once the team agrees: set `data.source: snapshot` in the config files.

Before loading, `DataStore.from_config` checks that `MANIFEST.json` exists and that every file
still matches its hash; a missing, changed or partly copied snapshot is refused with the list
of files to fix. Tests always use the synthetic fixtures.

## Publish a draft snapshot for the team (issue #36)
1. Build: `make snapshot-build` (whole US) or `make snapshot-build ARGS="--states AZ"` (trial
   region). Do **not** use `--freeze` for a draft.
2. Report + zip: `make snapshot-report ARGS="--pack results/snapshot-v1-draft.zip --out results/snapshot-report.md"`
   (`results/` is git-ignored, so the zip can never be committed by accident).
   It re-checks the manifest, re-runs the validator and refuses to pack if anything blocks.
3. On GitHub: **Releases → Draft a new release**, tag `snapshot-v1-draft`, tick **Set as a
   pre-release**, attach `results/snapshot-v1-draft.zip`, publish.
4. Paste `results/snapshot-report.md` into issue #36 with the release link. It contains every item the
   issue asks for: retrieval windows, counts, warnings, `verify_manifest()` result, coverage of
   the benchmark requests and the `frozen` flag.
5. Do not commit the snapshot files (git-ignored). Teammates use the release + `make snapshot-verify`.

## Routes / OSRM (routes/ starts empty)
`routing.mode: cache` (the default in configs A/B/C) only reads pre-made fixture routes, so it
cannot route real snapshot trips. To plan on the snapshot:

1. Set `OSRM_BASE_URL` in `.env`. `https://router.project-osrm.org` (in `.env.example`) is
   OSRM's public **demo** server: fine for a few trial trips, not for the full benchmark (it
   asks for light use). For the evaluation run, host OSRM yourself, e.g. with Docker and a
   Geofabrik extract:
   ```
   wget https://download.geofabrik.de/north-america/us/arizona-latest.osm.pbf
   docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/arizona-latest.osm.pbf
   docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/arizona-latest.osrm
   docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/arizona-latest.osrm
   docker run -t -p 5000:5000 -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld /data/arizona-latest.osrm
   ```
   then `OSRM_BASE_URL=http://localhost:5000`. A whole-US extract needs far more memory; the
   benchmark routes cross many states, so pick extracts that cover them.
2. Copy `configs/config_a.yaml` to `results/config_a_snapshot.yaml` (git-ignored) and change
   `data.source: snapshot` and `routing.mode: osrm`.
3. Run `python -m clickless plan --config results/config_a_snapshot.yaml "..."`.

OSRM answers are cached in `data/cache/osrm/` (git-ignored) by the cache-first router, not in
`routes/`. Details: ADR 0010 (stations, places) and ADR 0011 (amenities).
