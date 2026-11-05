# data/snapshot/ (not in git, except this README and MANIFEST.json)

## Use a snapshot someone already built
Download the snapshot files from the team drive / GitHub release into this folder, then run
`python -c "from clickless.data.snapshot import verify_manifest; print(verify_manifest())"` --
an empty list means your copy matches MANIFEST.json.

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

Details: ADR 0010 (stations, places) and ADR 0011 (amenities). `routes/` starts empty; OSRM
routes are cached there by the router at run time.
