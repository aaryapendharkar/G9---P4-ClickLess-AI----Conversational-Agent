# data/ (owner: Aarya)

| Folder | In git? | What |
|---|---|---|
| `fixtures/` | yes | Tiny **synthetic** Phoenix→Flagstaff world. Everyone develops and tests against this until the real snapshot exists. Never cite it as real data. |
| `curated/vehicles.csv` | yes | The ~15 supported model-year variants. One source URL per curated value + access date (proposal §II). |
| `raw/` | no | Raw API pulls (AFDC JSON, Overpass JSON). Re-creatable. |
| `snapshot/` | no (only `MANIFEST.json`) | The **frozen** normalized snapshot every configuration reads. Share the files via the team drive / a GitHub release; commit only the manifest so anyone can check hashes. |
| `cache/` | no | OSRM / geocoder caches. |

Snapshot layout must match fixtures layout (`stations.json`, `vehicles.json`, `amenities.json`, `places.json`, `routes/`) so switching `data.source: fixtures → snapshot` in a config is the only change needed.

Freeze procedure: `make snapshot` → writes `snapshot/MANIFEST.json` (SHA-256 per file, retrieval timestamps, record counts). After the test-set freeze, the snapshot is never regenerated; fixes go in a new snapshot version + ADR.
