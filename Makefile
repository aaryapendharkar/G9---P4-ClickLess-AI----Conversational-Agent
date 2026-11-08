.PHONY: setup test todo lint demo eval-dev bench-check snapshot snapshot-build snapshot-report snapshot-verify

setup:            ## install in editable mode with dev tools
	pip install -e ".[dev]"

test:             ## full suite; xfail = TODO specs, XPASS = you finished one -> remove its xfail
	pytest

todo:             ## list every open spec (xfail) with its owner
	pytest -rx -q | grep -E "XFAIL|xfail" || true

lint:
	ruff check src tests

demo:             ## Config A on the synthetic fixture world
	python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at least 20%, lunch stop and a wheelchair accessible restroom please"

eval-dev:         ## run the harness on the dev split (CONFIG=A|B|C)
	python -m evaluation.harness --config $${CONFIG:-A} --split dev

bench-check:      ## validate every benchmark JSONL file
	python -c "from evaluation.schema import load_items; [print(s, len(load_items('benchmark/requests/'+s))) for s in ('raw','dev','test')]"

snapshot:         ## check data/snapshot against MANIFEST.json (the build writes the manifest; never re-hash by hand)
	python -m clickless.data.snapshot_report --verify-only

snapshot-build:   ## download AFDC + OSM + geocodes and build data/snapshot (needs internet + NREL_API_KEY in .env)
	python -m clickless.data.build_snapshot $(ARGS)

snapshot-report:  ## Markdown report of data/snapshot for issue #36 (ARGS="--pack snapshot.zip" to also zip it)
	python -m clickless.data.snapshot_report $(ARGS)

snapshot-verify:  ## after downloading a shared snapshot: every file must match MANIFEST.json
	python -m clickless.data.snapshot_report --verify-only
