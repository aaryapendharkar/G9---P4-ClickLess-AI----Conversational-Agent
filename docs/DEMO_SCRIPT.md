# Presentation demo script

Six scenarios, all on the snapshot with Config C (Qwen3 8B parser + KG evidence + source labels).
Every scenario below uses places that are in the snapshot gazetteer and routes that are in the route
cache. The demo-blocking fixes listed below were merged by Nov 23. Run all six scenarios once from
the exact presentation commit on the presentation machine: Config C depends on the local Qwen
model, its pinned version and the frozen snapshot, so documented outputs are expectations rather
than a substitute for that final dry run.

Weak spots this script avoids on purpose:
- "cheapest": there is no cheapest profile yet, so it plans as balanced.
- Any city pair not listed here: if a route isn't cached, the trip cannot be planned. The CLI now
  gives a friendly route-data message instead of exposing an exception (#142).
- Unscripted model phrasing. Use the requests below so parser and route-cache behavior remain
  reproducible; clarification answers such as "about 70%" are supported (#135).

## Setup before the demo

Do this on the demo machine at least 15 minutes before starting. Commands are for PowerShell; Bash equivalents are in comments.

1. **Code:** `git switch main; git pull`. Record the commit used for the demo.
2. **Snapshot unzipped and verified.** Unzip the final `snapshot-v1.zip` release into
   `data/snapshot/`, so that
   `data/snapshot/MANIFEST.json`, `stations.json` and `routes/` exist. Then run:
   ```powershell
   python -m clickless.data.snapshot_report --verify-only     # = make snapshot-verify
   ```
   It must print `verify_manifest() -> [] (intact)`.
3. **Cached routes for all six scenarios.** Run this one check; it must print six `True` lines:
   ```powershell
   python -c "from pathlib import Path; r=Path('data/snapshot/routes'); [print(f, (r/f).is_file()) for f in ['downtown-los-angeles__santa-monica.json','los-angeles__san-francisco.json','phoenix__flagstaff.json','flagstaff__page.json','ely__tonopah.json','phoenix__tucson.json']]"
   ```
   The route file name comes from the place names as the parser writes them. If Qwen writes "Phoenix, AZ", the file is `phoenix-az__tucson.json` (or `phoenix-az__tucson-az.json`). The snapshot caches the bare, `-st` and mixed variants of every pair, so all of them are covered.
4. **Ollama is running with `qwen3:8b` at the pinned digest.**
   ```powershell
   ollama list            # the qwen3:8b line must show ID 500a1f067a9f
   ```
   If the ID differs, run `ollama pull qwen3:8b`. If the ID still differs, stop: the demo and the
   evaluation must use the same model (`docs/VOYAGER_LOCAL.md`).
5. **Environment** (the app does not read `.env` by itself):
   ```powershell
   $env:CLICKLESS_DATA_SOURCE = "snapshot"
   $env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
   $env:VOYAGER_API_KEY = ""
   # Bash: export CLICKLESS_DATA_SOURCE=snapshot; set -a; source .env; set +a
   ```
6. **Warm up** (the first Config C request after a cold start took about 4 minutes in the Nov 21 dry run):
   ```powershell
   # load the model and keep it loaded for 2 h
   curl.exe http://localhost:11434/api/generate -d '{\"model\": \"qwen3:8b\", \"prompt\": \"OK\", \"keep_alive\": \"2h\", \"think\": false, \"options\": {\"num_predict\": 1}}'
   # one full request, not shown to the audience (also fills the SBERT amenity cache)
   python -m clickless plan --config C --no-input "From Downtown Los Angeles to Santa Monica in my Model 3 Long Range, I have 25% battery and want to arrive with at least 20%, and I'd like a coffee shop while I charge"
   ```
   Then run scenario 1 once more. It should answer within a few seconds.
7. **CLI flags:**
   - `python -m clickless plan --config C "<request>"` is the live form. **Don't** pass `--no-input` during the demo; without it the CLI asks clarification questions and offers the Google Maps hand-off.
   - `--json` prints the whole `PlanResult`: parsed request, notes, the full list of stations not used, and timings. Use it to debug, not on screen.
   - After a plan, the CLI asks `Approve handoff? Enter option number (1 = Recommended) or press Enter to skip:`. Type `1` once, in scenario 1, to show the Google Maps link. Press Enter in the others.
8. **Terminal:** a large font and a wide window. Run `cls` between scenarios.

## Implemented demo safeguards

These are already merged; the table explains why each behavior matters during the demo.

| Scenario | Implemented by | Behavior protected |
|---|---|---|
| all | #123 | final snapshot verifies on a fresh checkout |
| all | #143 | one concise `Not used:` summary instead of raw station IDs |
| all (safety net) | #142 | an uncached route produces a friendly explanation |
| 2 long multi-stop and 6 trade-off | #133 | usable stations are distributed along the route instead of clustering near the origin |
| 3 clarification | #141, #135 | the CLI continues the clarification session and accepts natural answers such as `about 70%` |
| 4 accessibility | #150, #147 | a required accessible restroom is enforced and unnamed matching amenities are still explained |

## Scenarios

### 1. Urban trip with an amenity (Config C)

```powershell
python -m clickless plan --config C "From Downtown Los Angeles to Santa Monica in my Model 3 Long Range, I have 25% battery and want to arrive with at least 20%, and I'd like a coffee shop while I charge"
```
- **Follow-up:** at `Approve handoff?` type `1`. This prints the Google Maps link.
- **What it should show:** one short stop at a fast charger the Model 3 can use, the source label `AFDC, cached snapshot`, and `Nearby (requested):` with named cafés. Then the "Why this ranks first" line and one `Not used: N stations on the route (…)` line.
  - **Nov 21 dry run (main):** a Tesla Supercharger in Los Angeles (NACS, 250 kW), arrival with about 22% after about 33 min, and an "Other options" line ("Least detour … loses coffee").
  - The route-distributed candidate policy keeps at most 2 usable chargers per ~25 km stretch, so
    the exact stop, count and alternative line may differ from the historical Nov 21 output.
    Record what the final dry run shows.
- **Route cache:** `downtown-los-angeles__santa-monica.json`.
- **Talking point:** the coffee shop is matched against OSM amenities near the charger, and every number comes from the verified plan.

### 2. Long multi-stop trip (Config C)

```powershell
python -m clickless plan --config C "Los Angeles to San Francisco in my Chevy Bolt EUV, 90% battery, arrive with at least 15%, and a lunch stop on the way"
```
- **Follow-up:** press Enter at `Approve handoff?`.
- **What it should show:** at least 2 charging stops spread along the route (I-5 / CA-99), arriving in San Francisco with at least 15%. Total about 8.5 h: Config A measured 507 min on #133. Config C's ranker may choose slightly differently. If a stop has food nearby, it is listed as requested. Planning time should be a few seconds (4 s measured with #133).
- **Route cache:** `los-angeles__san-francisco.json`.
- **Talking point:** before #106 / #133, the first 60 candidates were all within 2 km of downtown LA, so this trip came back infeasible.

### 3. Incomplete request, then a clarification answer (Config C)

```powershell
python -m clickless plan --config C "Phoenix to Flagstaff in my IONIQ 5"
```
- **Follow-up:** the CLI asks `What percentage battery do you have right now? …`. Type `about 70%`.
- **What it should show:** the question, then a full plan for Phoenix → Flagstaff using the 70% answer. If Config C asks a second question (e.g. arrival battery), answer `20%`.
- **Route cache:** `phoenix__flagstaff.json`.
- **Talking point:** Config C asks for safety-critical values instead of guessing them. Config B would only answer "missing: start_soc_pct".

### 4. Accessibility need (Config C)

```powershell
python -m clickless plan --config C "Flagstaff to Page in my Kia EV6, 60% battery, arrive with at least 20%, I need a wheelchair accessible restroom at the stop"
```
- **Follow-up:** press Enter at `Approve handoff?`.
- **What it should show:** the recommended stop has a wheelchair-accessible restroom, listed under `Nearby (requested):`, by name or as "an accessible restroom" if it has no name (#137). The "Why this ranks first" line counts it under "requested amenities". Arrival in Page with at least 20%.
- **Route cache:** `flagstaff__page.json`.
- **Regression to check:** before #150 and #147, the recommended stop could omit the required
  restroom or fail to name an unnamed matching amenity. The final dry run must show the accessible
  restroom on the recommended plan before using this scenario on stage.

### 5. Infeasible request with a grounded reason (Config C)

```powershell
python -m clickless plan --config C "Ely to Tonopah in my Nissan Leaf, 10% battery, arrive with at least 60%, CCS chargers only"
```
- **Follow-up:** none.
- **What it should show:** no plan, and the reason taken from the vehicle record: `2024 Nissan LEAF SV PLUS cannot use the requested connector(s): CCS1. This vehicle supports: CHADEMO, J1772. Try one of the supported connectors.`
- **Route cache:** `ely__tonopah.json`.
- **Talking point:** the system says *why* no plan exists instead of inventing one.
- **Check in the dry run:** the Nov 21 run used "only 350 kW chargers" and the parser also added CCS1. The explanation then talked about a connector the presenter never mentioned, so this script asks for CCS explicitly. Make sure the parsed request (`--json`) has `connectors_override: ["CCS1"]`.

### 6. Priority trade-off: "fastest" vs "no rush" (Config C)

Run the same trip twice; only the last phrase changes.
```powershell
python -m clickless plan --config C "Phoenix to Tucson in my Mustang Mach-E, 30% battery, arrive with at least 20%, with lunch on the way, fastest option please"
python -m clickless plan --config C "Phoenix to Tucson in my Mustang Mach-E, 30% battery, arrive with at least 20%, with lunch on the way, no rush, I don't mind a longer stop"
```
- **Follow-up:** press Enter at `Approve handoff?` both times.
- **What it should show:** the "Why this ranks first" line says `fastest priorities` in the first run and `comfort priorities` in the second.
  - **Fastest:** lunch is worth 3 min, detours count fully and each stop costs 8 min, so the quickest stop wins.
  - **No rush:** lunch is worth 30 min and charging time counts at 0.6, so a stop with food is preferred even if it takes a little longer.
  - If both runs pick the same stop, show that the weights in the "Why" line differ and move on.
- **Route cache:** `phoenix__tucson.json`.
- **Talking point:** the driver's words map to explicit, inspectable weights (`configs/tradeoffs.yaml`), not a black box. Don't say "cheapest": it is not a profile yet and plans as balanced.

## If something goes wrong on stage
- **A question you didn't expect:** answer it, or press Ctrl+C and move to the next scenario.
- **"I don't have route data for X -> Y yet":** the parser wrote a place name the cache doesn't have. Re-type the request with the exact wording above.
- **Very slow first answer:** the model was unloaded. Run the warm-up `curl` again (setup step 6).
